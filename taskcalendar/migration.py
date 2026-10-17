"""
기존 폴더 구조를 data/ 하위의 체계적인 업무별 구조로 안전하게 1회 자동 마이그레이션한다.

새 폴더 구조:
- data/db/                     : 메타 DB 및 진단 로그
- data/calendar/attachments/   : 캘린더 일정/할일 첨부파일 (날짜별: YYYY/MM/DD)
- data/memo/attachments/       : 일반 메모 첨부파일
- data/documents/attachments/  : 관련 업무/Task 첨부파일
- data/browser/                : 브라우저 플로팅 메모 백업 (page_memos.json)
- data/work/templates/         : 업무 표준 서식 템플릿
- data/work/attachments/       : 업무 전용 첨부파일 (work/<그룹>/<분류>/<업무명>/...)
- data/work/<그룹>/<번호>.<분류>/ : 실제 업무 문서 파일 (.hwpx 등)
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path

from taskcalendar.paths import (
    browser_root,
    calendar_attachments_root,
    data_root,
    db_dir,
    db_path,
    document_attachments_root,
    memo_attachments_root,
    runtime_root,
    work_attachments_root,
    work_root,
    work_templates_root,
)
from taskcalendar.work_files import (
    format_numbered_category,
    sanitize_component,
    strip_category_prefix,
    unique_path,
)

logger = logging.getLogger(__name__)


def _move_or_merge_dir(src: Path, dst: Path) -> None:
    """src 디렉토리의 파일과 하위 폴더들을 dst로 이동/병합한다."""
    if not src.exists() or not src.is_dir():
        return
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None

    dst.mkdir(parents=True, exist_ok=True)
    for idx, item in enumerate(list(src.iterdir())):
        if idx % 10 == 0 and app:
            app.processEvents()
        target = dst / item.name
        try:
            if item.is_dir():
                if target.exists():
                    _move_or_merge_dir(item, target)
                else:
                    shutil.move(str(item), str(target))
            else:
                if target.exists() and target.resolve() != item.resolve():
                    try:
                        if target.read_bytes() == item.read_bytes():
                            item.unlink()
                            continue
                    except Exception:
                        pass
                    if item.suffix.lower() == ".txt" and target.with_suffix(".hwpx").exists():
                        try:
                            item.unlink()
                            continue
                        except Exception:
                            pass
                    target = unique_path(target)
                if target.resolve() != item.resolve():
                    shutil.move(str(item), str(target))
        except Exception as e:
            logger.warning("Failed to move %s -> %s: %s", item, target, e)
    # 비어 있으면 삭제
    try:
        if not any(src.iterdir()):
            src.rmdir()
    except Exception:
        pass


def _get_category_sort_orders(db_file: Path) -> dict[tuple[int, str], int]:
    """
    암호화 DB를 복호화 메모리 DB로 로드하거나 스키마를 읽어 (tab_id, clean_name) -> sort_order 매핑을 만든다.
    """
    mapping: dict[tuple[int, str], int] = {}
    if not db_file.exists():
        return mapping
    try:
        from taskcalendar.storage import unprotect_bytes

        raw_enc = db_file.read_bytes()
        plain_bytes = unprotect_bytes(raw_enc)
        conn = sqlite3.connect(":memory:")
        try:
            conn.deserialize(plain_bytes)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT name, tab_id, sort_order FROM work_categories").fetchall()
            for r in rows:
                tab = int(r["tab_id"] or 1)
                name = str(r["name"] or "").strip()
                clean = strip_category_prefix(name)
                order = int(r["sort_order"] or 1)
                mapping[(tab, clean)] = order
                mapping[(0, clean)] = order  # 탭 무관 fallback
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Could not read category sort orders from DB: %s", e)
    return mapping


def _get_category_metadata(db_file: Path) -> dict[str, dict]:
    """
    clean_name -> {"tab_id": int, "parent_name": str, "sort_order": int} 매핑을 생성한다.
    """
    metadata: dict[str, dict] = {}
    if not db_file.exists():
        return metadata
    try:
        from taskcalendar.storage import unprotect_bytes

        raw_enc = db_file.read_bytes()
        plain_bytes = unprotect_bytes(raw_enc)
        conn = sqlite3.connect(":memory:")
        try:
            conn.deserialize(plain_bytes)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, name, parent_id, tab_id, sort_order FROM work_categories").fetchall()
            id_map = {r["id"]: r for r in rows}
            for r in rows:
                clean = strip_category_prefix(str(r["name"] or "").strip())
                if not clean:
                    continue
                tab = int(r["tab_id"] or 1)
                order = int(r["sort_order"] or 1)
                p_id = r["parent_id"]
                parent_name = ""
                if p_id and p_id in id_map:
                    pr = id_map[p_id]
                    p_clean = strip_category_prefix(str(pr["name"] or "").strip())
                    p_order = int(pr["sort_order"] or 1)
                    parent_name = f"{p_order}.{p_clean}"
                metadata[clean] = {
                    "tab_id": tab,
                    "parent_name": parent_name,
                    "sort_order": order,
                }
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Could not read category metadata from DB: %s", e)
    return metadata


def _update_migration_progress(msg: str, val: int, prog_state: list) -> None:
    """마이그레이션 진행 상태 로깅 (프로그램 시작 시 사용자 방해 모달 팝업 억제)."""
    logger.info("[Data Migration %d%%] %s", val, msg)


def _clean_and_merge_work_dirs(work_root_dir: Path, cat_meta_map: dict) -> None:
    """
    data/work 하위의 모든 폴더명을 깔끔한 분류명(숫자 접두사 제거)으로 통합하고,
    오배치된 탭 폴더를 올바른 탭으로 이전한다.
    """
    if not work_root_dir.exists() or not work_root_dir.is_dir():
        return
    reserved_names = {"attachments", "attach", "browser", "templates"}

    # 1. 탭 간 오배치된 폴더 이전
    for grp_dir in list(work_root_dir.iterdir()):
        if not grp_dir.is_dir() or not grp_dir.name.isdigit():
            continue
        curr_tab = int(grp_dir.name)
        for cat_dir in list(grp_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.lower() in reserved_names:
                continue
            clean = strip_category_prefix(cat_dir.name)
            meta = cat_meta_map.get(clean)
            if meta and meta.get("tab_id") and meta["tab_id"] != curr_tab:
                tgt_tab = meta["tab_id"]
                p_name = strip_category_prefix(meta.get("parent_name", ""))
                tgt_parent = work_root_dir / str(tgt_tab)
                if p_name:
                    tgt_parent = tgt_parent / p_name
                target_dir = tgt_parent / clean
                if target_dir.resolve() != cat_dir.resolve():
                    _move_or_merge_dir(cat_dir, target_dir)

    # 2. 깊은 하위 폴더부터 번호 접두사를 제거하여 clean_name으로 병합/이름 변경
    for grp_dir in list(work_root_dir.iterdir()):
        if not grp_dir.is_dir() or not grp_dir.name.isdigit():
            continue
        all_dirs = sorted(
            [d for d in grp_dir.rglob("*") if d.is_dir() and d.name.lower() not in reserved_names],
            key=lambda d: len(d.parts),
            reverse=True,
        )
        for d in all_dirs:
            if not d.exists():
                continue
            name = d.name
            clean = strip_category_prefix(name)
            if clean != name:
                target = d.parent / clean
                if target.resolve() != d.resolve():
                    _move_or_merge_dir(d, target)


def migrate_to_data_root() -> None:
    """
    기존 루트에 흩어져 있던 db, work, attachments, 업무 템플릿 폴더들을
    data/ 하위의 새 분류 체계로 안전하게 이전한다.
    """
    root = runtime_root()
    d_root = data_root()

    old_db_dir = root / "db"
    old_work_dir = root / "work"
    root_att = root / "attachments"
    old_tpl_dir = root / "업무 템플릿"

    # 실제 구버전 폴더가 존재하는지 확인하여 필요할 때만 프로그레스 창을 표시
    has_legacy = any(
        d.exists() and d.is_dir() and d.resolve() != d_root.resolve() and any(d.iterdir())
        for d in (old_db_dir, old_work_dir, root_att, old_tpl_dir)
    )
    prog_state = []
    if has_legacy:
        _update_migration_progress("새로운 data/ 저장소 구조로 안전하게 이전하고 있습니다...", 5, prog_state)

    # 0. 필수 기본 디렉토리 무조건 생성
    try:
        if has_legacy:
            _update_migration_progress("필수 기본 디렉토리를 생성하고 있습니다...", 10, prog_state)
        data_root().mkdir(parents=True, exist_ok=True)
        db_dir().mkdir(parents=True, exist_ok=True)
        calendar_attachments_root().mkdir(parents=True, exist_ok=True)
        memo_attachments_root().mkdir(parents=True, exist_ok=True)
        document_attachments_root().mkdir(parents=True, exist_ok=True)
        browser_root().mkdir(parents=True, exist_ok=True)
        work_root().mkdir(parents=True, exist_ok=True)
        work_templates_root().mkdir(parents=True, exist_ok=True)
        work_attachments_root().mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Could not ensure basic data folders: %s", e)

    # 1. DB 파일 마이그레이션: <root>/db/ -> <root>/data/db/
    old_db_dir = root / "db"
    new_db_dir = db_dir()
    if old_db_dir.exists() and old_db_dir.resolve() != new_db_dir.resolve():
        if has_legacy:
            _update_migration_progress("데이터베이스 파일을 data/db 로 이전하고 있습니다...", 20, prog_state)
        old_db = old_db_dir / "taskcalendar.db.enc"
        new_db = db_path()
        if old_db.exists() and not new_db.exists():
            new_db_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(old_db), str(new_db))
                logger.info("Migrated DB file to %s", new_db)
            except Exception as e:
                logger.error("Failed to move DB file: %s", e)

        # 진단 로그, .enc, .bak 및 백업 파일 일체 이전
        for item in list(old_db_dir.iterdir()):
            if item.is_file() and any(item.name.endswith(ext) for ext in (".log", ".enc", ".bak", ".safety_backup")):
                target_f = new_db_dir / item.name
                if not target_f.exists():
                    try:
                        shutil.move(str(item), str(target_f))
                    except Exception:
                        pass
        old_backups = old_db_dir / "backups"
        if old_backups.exists() and old_backups.is_dir():
            _move_or_merge_dir(old_backups, new_db_dir / "backups")

        # old_db_dir / attachments 가 있으면 calendar 첨부로 이전
        old_db_att = old_db_dir / "attachments"
        if old_db_att.exists() and old_db_att.is_dir():
            _move_or_merge_dir(old_db_att, calendar_attachments_root())

    # 2. 루트 직하의 attachments/ 가 있으면 calendar 첨부로 이전
    root_att = root / "attachments"
    if root_att.exists() and root_att.is_dir() and root_att.resolve() != calendar_attachments_root().resolve():
        if has_legacy:
            _update_migration_progress("첨부파일을 data/calendar 로 이전하고 있습니다...", 35, prog_state)
        _move_or_merge_dir(root_att, calendar_attachments_root())

    # 3. 브라우저 메모 마이그레이션:
    #    work/browser/page_memos.json -> data/browser/page_memos.json
    old_work_dir = root / "work"
    new_work_dir = work_root()
    new_browser_dir = browser_root()

    # 기존 work/browser 가 있으면 data/browser 로 이동
    if old_work_dir.exists():
        old_work_browser = old_work_dir / "browser"
        if old_work_browser.exists():
            if has_legacy:
                _update_migration_progress("브라우저 메모를 이전하고 있습니다...", 45, prog_state)
            _move_or_merge_dir(old_work_browser, new_browser_dir)

    # 4. 업무 서식 템플릿 마이그레이션:
    #    <root>/업무 템플릿/ -> <root>/data/work/templates/
    new_tpl_dir = work_templates_root()
    old_tpl_dir = root / "업무 템플릿"
    if old_tpl_dir.exists() and old_tpl_dir.is_dir() and old_tpl_dir.resolve() != new_tpl_dir.resolve():
        if has_legacy:
            _update_migration_progress("업무 서식 템플릿을 이전하고 있습니다...", 55, prog_state)
        try:
            for item in old_tpl_dir.iterdir():
                target = new_tpl_dir / item.name
                if not target.exists():
                    if item.is_dir():
                        shutil.copytree(str(item), str(target), dirs_exist_ok=True)
                    else:
                        shutil.copy2(str(item), str(target))
            logger.info("Migrated templates to %s", new_tpl_dir)
        except Exception as e:
            logger.warning("Template migration warning: %s", e)

    # 5. 업무 전용 첨부파일 마이그레이션:
    #    work/attach/ -> data/work/attachments/
    new_work_att_dir = work_attachments_root()
    if old_work_dir.exists():
        old_work_attach = old_work_dir / "attach"
        if old_work_attach.exists() and old_work_attach.is_dir():
            if has_legacy:
                _update_migration_progress("업무 첨부파일을 data/work/attachments 로 이전하고 있습니다...", 65, prog_state)
            _move_or_merge_dir(old_work_attach, new_work_att_dir)

    # 6. 업무 본문 문서 및 분류 폴더 번호 체계 마이그레이션:
    #    work/1/인사 -> data/work/1/1.인사/ (번호 자동 부여)
    if has_legacy:
        _update_migration_progress("업무 문서 및 분류 폴더 체계를 마이그레이션하고 있습니다...", 75, prog_state)
    cat_order_map = _get_category_sort_orders(db_path())
    cat_meta_map = _get_category_metadata(db_path())

    def _migrate_group_categories(src_grp_dir: Path, dst_grp_dir: Path, tab_id: int):
        if not src_grp_dir.exists() or not src_grp_dir.is_dir():
            return
        dst_grp_dir.mkdir(parents=True, exist_ok=True)
        # 시스템 디렉토리는 제외
        reserved_names = {"attachments", "attach", "browser", "templates"}
        subdirs = [d for d in src_grp_dir.iterdir() if d.is_dir() and d.name.lower() not in reserved_names]

        # 폴더 정렬
        subdirs.sort(key=lambda d: d.name)
        for d in subdirs:
            raw_name = d.name
            clean_name = strip_category_prefix(raw_name)
            target_cat_dir = dst_grp_dir / clean_name
            if target_cat_dir.resolve() != d.resolve():
                _move_or_merge_dir(d, target_cat_dir)

        # 그룹 디렉토리 직하의 고아 파일들도 이동
        for f in src_grp_dir.iterdir():
            if f.is_file():
                dest_f = dst_grp_dir / f.name
                if dest_f.resolve() != f.resolve():
                    if dest_f.exists():
                        dest_f = unique_path(dest_f)
                    shutil.move(str(f), str(dest_f))

    # old_work_dir (<root>/work) 와 new_work_dir (<root>/data/work) 모두 처리
    dirs_to_check = []
    if old_work_dir.exists() and old_work_dir.resolve() != new_work_dir.resolve():
        dirs_to_check.append(old_work_dir)
    if new_work_dir.exists():
        dirs_to_check.append(new_work_dir)

    for w_dir in dirs_to_check:
        for item in list(w_dir.iterdir()):
            if not item.is_dir():
                continue
            name = item.name
            if name.lower() in ("attachments", "attach", "browser", "templates"):
                continue
            # 그룹 번호 (1, 2, 3...) 폴더
            if name.isdigit():
                tab_num = int(name)
                dst_grp = new_work_dir / name
                _migrate_group_categories(item, dst_grp, tab_num)
            else:
                # 탭 구분 없이 1차 폴더가 카테고리인 레거시 구조 처리
                clean_name = strip_category_prefix(name)
                meta = cat_meta_map.get(clean_name)
                target_tab = meta["tab_id"] if meta else 1
                parent_name = strip_category_prefix(meta.get("parent_name", "")) if meta else ""

                dst_folder = new_work_dir / str(target_tab)
                if parent_name:
                    dst_folder = dst_folder / parent_name
                target_cat_dir = dst_folder / clean_name
                if target_cat_dir.resolve() != item.resolve():
                    _move_or_merge_dir(item, target_cat_dir)

    # 첨부 디렉토리(work/attachments/) 하위의 그룹별 카테고리 폴더도 정리
    if new_work_att_dir.exists():
        for grp_dir in list(new_work_att_dir.iterdir()):
            if grp_dir.is_dir() and grp_dir.name.isdigit():
                tab_num = int(grp_dir.name)
                _migrate_group_categories(grp_dir, grp_dir, tab_num)

    # 6-1. data/work 및 data/work/attachments 하위의 폴더명 정제(번호 제거) 및 오배치 정리
    _clean_and_merge_work_dirs(new_work_dir, cat_meta_map)
    if new_work_att_dir.exists():
        _clean_and_merge_work_dirs(new_work_att_dir, cat_meta_map)

    # 7. data 루트 직하의 레거시 파일 정리
    stale_root_db = d_root / "taskcalendar.db.enc"
    if stale_root_db.exists() and new_db_dir.exists():
        new_real_db = db_path()
        if new_real_db.exists() and stale_root_db.resolve() != new_real_db.resolve():
            # stale_root_db 백업 폴더로 격리
            bak_dir = d_root / "backups"
            bak_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(stale_root_db), str(unique_path(bak_dir / "taskcalendar_legacy_root.db.enc")))
            except Exception:
                pass

    stale_root_att = d_root / "attachments"
    if stale_root_att.exists() and stale_root_att.is_dir():
        _move_or_merge_dir(stale_root_att, calendar_attachments_root())

    # 8. DB 내 첨부파일 경로 갱신 및 중복 카테고리 정리
    if has_legacy:
        _update_migration_progress("데이터베이스 첨부파일 및 분류 체계를 정리하고 있습니다...", 90, prog_state)
    try:
        from taskcalendar.storage import EncryptedRepository
        repo = EncryptedRepository(db_path())
        repo.migrate_work_attachments_into_work_tree()
        repo.cleanup_duplicate_work_categories()
    except Exception as e:
        logger.debug("Work attachments DB path migration error: %s", e)

    # 9. 비어 있는 구 디렉토리 정리
    for old_d in (old_db_dir, old_work_dir, root_att):
        try:
            if old_d.exists() and old_d.is_dir() and old_d.resolve() != d_root.resolve() and not any(old_d.iterdir()):
                old_d.rmdir()
                logger.info("Removed empty legacy directory: %s", old_d)
        except Exception:
            pass

    if prog_state:
        _update_migration_progress("마이그레이션이 성공적으로 완료되었습니다.", 100, prog_state)
        try:
            prog_state[0].close()
        except Exception:
            pass
