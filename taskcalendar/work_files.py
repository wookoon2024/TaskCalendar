"""
업무 파일(work/) 관리 — 파일명 정제, 경로 규칙, OS 열기.

설계(계획서 work-filesystem-hybrid.md) 1단계 기반 모듈:
- 업무문서를 실제 파일로 다루기 위한 파일명/경로 규칙을 한 곳에 모은다.
- 아직 저장/로드 흐름은 DB blob을 그대로 쓰고, "한글로 열기"/"탐색기에서 열기"에서만 사용한다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 파일/폴더 이름 금지문자
_WINDOWS_FORBIDDEN = '\\/:*?"<>|'
# Windows 예약어(파일/폴더명으로 사용 불가)
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# 파일/폴더 이름 한 컴포넌트 최대 길이(경로 260자 여유 확보를 위한 보수적 상한)
MAX_COMPONENT_LEN = 120


import re


def strip_category_prefix(name: str) -> str:
    """'1.기본업무' 또는 '01. 기본업무' 등에서 앞의 숫자 접두사를 제거한 순수 이름 반환"""
    return re.sub(r"^\d+[\.\-_ ]\s*", "", (name or "").strip())


def format_numbered_category(name: str, sort_order: int | None = None) -> str:
    """
    카테고리 폴더명 반환.
    사용자 친화적인 폴더 구조를 위해 임의의 숫자 접두사 없이 깔끔한 분류명을 반환한다.
    """
    clean = strip_category_prefix(name)
    return clean or "미분류"


def work_root() -> Path:
    """업무 파일 루트 (데이터 루트 기준: data/work)"""
    from taskcalendar.paths import work_root as _work_root

    return _work_root()


def browser_root() -> Path:
    """브라우저 메모 및 연계 정보 루트 (data/browser)"""
    from taskcalendar.paths import browser_root as _browser_root

    return _browser_root()


def work_templates_root() -> Path:
    """업무 템플릿 서식 루트 (data/work/templates)"""
    from taskcalendar.paths import work_templates_root as _tpl_root

    return _tpl_root()


def work_attachments_root() -> Path:
    """업무 첨부파일 루트 (data/work/attachments)"""
    from taskcalendar.paths import work_attachments_root as _att_root

    return _att_root()


def browser_memos_path() -> Path:
    """브라우저 메모 기본 동기화 파일 (data/browser/page_memos.json)"""
    return browser_root() / "page_memos.json"


def load_browser_memos() -> list[dict]:
    """work/browser/page_memos.json에서 브라우저 메모 목록을 읽어 정규화된 리스트로 반환한다."""
    import json
    p = browser_memos_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "memos" in data and isinstance(data["memos"], list):
                return data["memos"]
            if "pages" in data and isinstance(data["pages"], dict):
                # 크롬 확장 프로그램 원본 store 구조 ({ url: { memos: [...] } }) 지원
                flat = []
                for p_key, page in data["pages"].items():
                    p_url = page.get("url") or p_key
                    p_title = page.get("title", "")
                    for m in page.get("memos", []):
                        m_copy = dict(m)
                        if "url" not in m_copy:
                            m_copy["url"] = p_url
                        if "title" not in m_copy or not m_copy["title"]:
                            m_copy["title"] = p_title
                        if "content" not in m_copy and "text" in m_copy:
                            m_copy["content"] = m_copy["text"]
                        flat.append(m_copy)
                return flat
    except Exception:
        pass
    return []


def save_browser_memos(memos: list[dict]) -> bool:
    """work/browser/page_memos.json에 브라우저 메모 목록을 저장한다."""
    import datetime
    import json
    p = browser_memos_path()
    try:
        payload = {
            "version": 1,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(memos),
            "memos": memos,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def merge_browser_memos(new_memos: list[dict], overwrite_existing: bool = False) -> tuple[int, int]:
    """
    기존 브라우저 메모에 새 메모들을 스마트 병합(Merge)한다.
    반환값: (새로 추가된 개수, 갱신된 개수)
    """
    existing = load_browser_memos()
    existing_map = {}
    for m in existing:
        mid = str(m.get("id") or "")
        key = mid if mid else (m.get("url", ""), m.get("content", ""), m.get("created_at", ""))
        existing_map[key] = m

    added_count = 0
    updated_count = 0

    for m in new_memos:
        mid = str(m.get("id") or "")
        key = mid if mid else (m.get("url", ""), m.get("content", ""), m.get("created_at", ""))
        if key in existing_map:
            if overwrite_existing:
                existing_map[key] = m
                updated_count += 1
        else:
            existing_map[key] = m
            added_count += 1

    merged = list(existing_map.values())
    save_browser_memos(merged)
    return added_count, updated_count


def export_browser_memos_to_file(target_path: Path | str, memos: list[dict] | None = None) -> bool:
    """브라우저 메모 목록을 지정한 파일(.json)로 내보낸다 (크롬 확장프로그램 [가져오기]와 100% 호환)."""
    import datetime
    import json
    tgt = Path(target_path)
    tgt.parent.mkdir(parents=True, exist_ok=True)
    if memos is None:
        memos = load_browser_memos()
    try:
        pages: dict[str, dict] = {}
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        for m in memos:
            url = str(m.get("url") or "https://unknown")
            if url not in pages:
                pages[url] = {
                    "url": url,
                    "ignoreParams": [],
                    "ignoreHash": False,
                    "schema": 1,
                    "createdAt": m.get("createdAt") or now_ms,
                    "memos": [],
                }
            m_copy = dict(m)
            # 크롬 확장프로그램 호환 필드 보정
            if "text" not in m_copy and "content" in m_copy:
                m_copy["text"] = m_copy["content"]
            if "content" not in m_copy and "text" in m_copy:
                m_copy["content"] = m_copy["text"]
            if "w" not in m_copy and "width" in m_copy:
                m_copy["w"] = m_copy["width"]
            if "h" not in m_copy and "height" in m_copy:
                m_copy["h"] = m_copy["height"]
            if "id" not in m_copy:
                m_copy["id"] = f"pm_{now_ms}_{len(pages[url]['memos'])}"
            pages[url]["memos"].append(m_copy)

        payload = {
            "app": "taskcalendar-page-memo",
            "version": 1,
            "exported_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "exportedAt": datetime.datetime.now().isoformat(),
            "count": len(memos),
            "pages": pages,
            "memos": memos,
        }
        tgt.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def import_browser_memos_from_file(source_path: Path | str, overwrite: bool = False) -> tuple[int, int]:
    """지정한 파일(.json)에서 브라우저 메모를 읽어와 현재 work/browser에 병합한다."""
    import json
    src = Path(source_path)
    if not src.exists():
        return 0, 0
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        if isinstance(data, list):
            memos = data
        elif isinstance(data, dict):
            if "memos" in data and isinstance(data["memos"], list):
                memos = data["memos"]
            elif "pages" in data and isinstance(data["pages"], dict):
                memos = []
                for p_key, page in data["pages"].items():
                    p_url = page.get("url") or p_key
                    p_title = page.get("title", "")
                    for m in page.get("memos", []):
                        m_copy = dict(m)
                        if "url" not in m_copy:
                            m_copy["url"] = p_url
                        if "title" not in m_copy or not m_copy["title"]:
                            m_copy["title"] = p_title
                        if "content" not in m_copy and "text" in m_copy:
                            m_copy["content"] = m_copy["text"]
                        memos.append(m_copy)
            else:
                memos = []
        else:
            memos = []
        return merge_browser_memos(memos, overwrite_existing=overwrite)
    except Exception:
        return 0, 0



def sanitize_component(name: str, fallback: str = "무제") -> str:
    """
    문자열을 Windows 파일/폴더 이름으로 안전하게 변환한다.
    - 금지문자(\\ / : * ? " < > |) → '_'
    - 제어문자 제거
    - 끝의 마침표/공백 제거
    - 예약어(CON, NUL, COM1 …) 회피
    - 컴포넌트 길이 상한 적용
    """
    s = (name or "").strip()
    for ch in _WINDOWS_FORBIDDEN:
        s = s.replace(ch, "_")
    s = "".join(c for c in s if ord(c) >= 32)
    s = s.rstrip(" .")
    if not s:
        s = fallback
    if s.split(".")[0].upper() in _RESERVED_NAMES:
        s = s + "_"
    if len(s) > MAX_COMPONENT_LEN:
        s = s[:MAX_COMPONENT_LEN].rstrip(" .")
    return s or fallback


def sheet_file_stem(title: str) -> str:
    """업무 제목 → 파일명 stem (확장자 제외, 정제 적용)"""
    t = (title or "").strip()
    for ext in (".hwpx", ".hwp", ".json", ".txt", ".md", ".csv", ".xml", ".html", ".htm", ".log", ".ini", ".yaml", ".yml"):
        if t.lower().endswith(ext):
            t = t[: -len(ext)]
            break
    return sanitize_component(t, "무제 업무")


def sheet_hwpx_path(group: str | int, category: str, title: str) -> Path:
    """업무문서 기본 저장 경로: work/<그룹번호>/<분류경로>/<제목>.hwpx"""
    return category_dir(group, category) / f"{sheet_file_stem(title)}.hwpx"


def unique_path(path: Path) -> Path:
    """이미 존재하면 ' (1)', ' (2)' … 를 붙인 경로를 반환"""
    if not path.exists():
        return path
    parent, stem, suffix = path.parent, path.stem, path.suffix
    i = 1
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def copy_into_work_tree(group: str | int, category: str, src_filename: str, src: Path | str) -> Path:
    """파일을 work/<그룹>/<분류>/ 트리로 복사한다(폴더 자동 생성, 이름 충돌 시 ' (n)')."""
    dest_dir = category_dir(group, category)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_component(Path(src_filename).stem, "무제 업무")
    dest = unique_path(dest_dir / f"{stem}{Path(src_filename).suffix}")
    s = Path(src)
    if s.resolve() != dest.resolve():
        shutil.copy2(s, dest)
    return dest


def sheet_file_exists(group: str | int, category: str, title: str) -> bool:
    """work/<그룹>/<분류>/<제목>.* 파일이 이미 있는지(에디터 포맷/기타 무관)"""
    dest = sheet_hwpx_path(group, category, title)
    if dest.exists() or dest.with_suffix(".txt").exists():
        return True
    return find_sheet_file(group, category, title) is not None


def migrate_items_to_files(items: list[dict], writer, progress_cb=None) -> int:
    """
    업무문서 dict 목록을 work/<분류>/<제목> 파일로 1회 이관한다.
    writer(item, dest_path) -> bool(성공) 를 호출해 실제 파일을 만든다
    (blob이 있으면 그대로, 없으면 텍스트로 HWPX 생성).
    이미 파일이 있으면 건너뛴다(멱등). progress_cb(i, total)가 False면 중단.
    반환: 새로 만든 파일 수.
    """
    done = 0
    total = len(items)
    for i, it in enumerate(items):
        if progress_cb is not None:
            try:
                if progress_cb(i, total) is False:
                    break
            except Exception:
                pass
        grp = it.get("group") or "1"
        cat = it.get("cat_path") or it.get("category_name") or "미분류"
        title = it.get("title") or "무제 업무"
        try:
            if sheet_file_exists(grp, cat, title):
                continue
            dest = sheet_hwpx_path(grp, cat, title)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if writer(it, dest):
                done += 1
        except Exception:
            continue
    return done


def file_sha256(path: Path | str) -> str:
    """파일 내용 해시(rename 자동 인식용)"""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


file_sha1 = file_sha256


SYSTEM_WORK_DIRS = {"attachments", "browser", "templates"}


def scan_work_files(known_categories: set | None = None) -> list[dict]:
    """
    work/ 트리에서 업무 항목(파일·폴더) 목록을 스캔한다.
    - attachments, templates, browser 등 시스템 예약 폴더는 제외
    - 폴더는 '업무 폴더'로 간주: 알려진 분류명이 아니고 깊이 3 이상일 때만
    각 항목: {path, group, category, stem, ext, size, sha1, is_dir}
    """
    root = work_root()
    out: list[dict] = []
    if not root.is_dir():
        return out
    known = {strip_category_prefix(c) for c in (known_categories or set()) if c}
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None

    for idx, p in enumerate(root.rglob("*")):
        if idx % 15 == 0 and app:
            app.processEvents()
        rel_parts = p.relative_to(root).parts
        if rel_parts and rel_parts[0] in SYSTEM_WORK_DIRS:
            continue  # 첨부, 템플릿, 브라우저 메모 등 시스템 디렉토리는 업무 항목이 아님
        group = rel_parts[0] if len(rel_parts) >= 3 else "1"
        try:
            cat = p.parent.relative_to(root / group).as_posix()
        except Exception:
            cat = p.parent.name
        try:
            if p.is_file():
                ext = p.suffix.lower()
                sha = ""
                # 업무 에디터 확장자이고 30MB 이하인 파일만 1회 해시 계산 (대용량/바이너리 병목 차단)
                if ext in EDITOR_EXTENSIONS:
                    try:
                        if p.stat().st_size <= 30 * 1024 * 1024:
                            sha = file_sha256(p)
                    except Exception:
                        sha = ""
                out.append({
                    "path": p, "group": group, "category": cat, "stem": p.stem,
                    "ext": ext, "size": p.stat().st_size, "sha256": sha, "sha1": sha, "is_dir": False,
                })
            elif p.is_dir() and len(rel_parts) >= 3 and strip_category_prefix(p.name) not in known:
                out.append({
                    "path": p, "group": group, "category": cat, "stem": p.name,
                    "ext": "", "size": 0, "sha256": "", "sha1": "", "is_dir": True,
                })
        except Exception:
            continue
    # 같은 업무에 원본(.hwp/.hwpx)과 텍스트 사본이 함께 있으면 텍스트는 목록에서 제외
    native_keys = {
        (f["group"], f["category"], f["stem"])
        for f in out
        if (not f["is_dir"]) and f["ext"] in NATIVE_EXTENSIONS
    }
    # .hwpx와 .hwp가 둘 다 있으면 최신 포맷인 .hwpx를 우선
    hwpx_keys = {
        (f["group"], f["category"], f["stem"])
        for f in out
        if (not f["is_dir"]) and f["ext"] == ".hwpx"
    }
    out = [
        f for f in out
        if f["is_dir"]
        or (
            (f["ext"] in NATIVE_EXTENSIONS or (f["group"], f["category"], f["stem"]) not in native_keys)
            and (f["ext"] != ".hwp" or (f["group"], f["category"], f["stem"]) not in hwpx_keys)
        )
    ]
    return out


def plan_reconcile(db_items: list[dict], disk_files: list[dict]) -> dict:
    """
    DB 업무문서와 work/ 파일을 비교한다.
    db_items: [{"id","title","category_name","sha256"}] (sha256 = blob 해시)
    disk_files: scan_work_files() 결과
    반환: {"matched":[(db,f)], "renamed":[(db,f)], "missing":[db], "new":[f]}
    - 경로(분류/제목)가 같으면 matched
    - 없는데 내용 해시가 같은 파일이 있으면 renamed(이름만 변경)
    - 남은 db → missing, 남은 파일 → new
    """
    used: set[int] = set()
    matched: list[tuple] = []
    renamed: list[tuple] = []
    missing_candidates: list[dict] = []
    for db in db_items:
        want_stem = sheet_file_stem(db.get("title") or "")
        want_cat = cat_relpath(db.get("cat_path") or db.get("category_name") or "미분류")
        want_cat_clean = "/".join(strip_category_prefix(p) for p in want_cat.split("/"))
        want_grp = sanitize_component(str(db.get("group") or "1"), "1")

        def _cat_matches(disk_cat: str) -> bool:
            if disk_cat == want_cat:
                return True
            disk_clean = "/".join(strip_category_prefix(p) for p in str(disk_cat).split("/"))
            return disk_clean == want_cat_clean

        hit = next(
            (f for f in disk_files if id(f) not in used and f.get("group") == want_grp
             and _cat_matches(f.get("category", "")) and f["stem"] == want_stem),
            None,
        )
        if hit:
            used.add(id(hit))
            matched.append((db, hit))
        else:
            missing_candidates.append(db)

    renamed_db: set[int] = set()
    for db in missing_candidates:
        sha = db.get("sha256") or db.get("sha1")
        hit = next((f for f in disk_files if id(f) not in used and sha and (f.get("sha256") == sha or f.get("sha1") == sha)), None)
        if hit:
            used.add(id(hit))
            renamed.append((db, hit))
            renamed_db.add(id(db))

    # 이미 matched 또는 renamed된 DB 업무와 동일한 (그룹, 순수분류경로, 제목)을 가진 남은 디스크 파일은
    # 중복 사본(예: 번호 폴더와 미부여 폴더가 공존하는 경우)이므로 'DB에 없는 새 파일'로 오인되지 않도록 제외
    resolved_keys = {
        (
            sanitize_component(str(db.get("group") or "1"), "1"),
            "/".join(strip_category_prefix(p) for p in cat_relpath(db.get("cat_path") or db.get("category_name") or "미분류").split("/")),
            sheet_file_stem(db.get("title") or "")
        )
        for db, _ in (matched + renamed)
    }

    missing = [db for db in missing_candidates if id(db) not in renamed_db]
    new = []
    for f in disk_files:
        if id(f) in used:
            continue
        f_cat_clean = "/".join(strip_category_prefix(p) for p in str(f.get("category", "")).split("/"))
        f_grp = sanitize_component(str(f.get("group") or "1"), "1")
        f_stem = sheet_file_stem(f.get("stem") or "")
        if (f_grp, f_cat_clean, f_stem) in resolved_keys:
            continue
        new.append(f)

    return {"matched": matched, "renamed": renamed, "missing": missing, "new": new}


NATIVE_EXTENSIONS = (".hwp", ".hwpx")          # 내장 에디터의 원본 포맷
TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".hml")  # 텍스트(에디터로 열림)
EDITOR_EXTENSIONS = NATIVE_EXTENSIONS + TEXT_EXTENSIONS


ATTACH_ROOT = "attachments"  # 업무 첨부 전용 루트: data/work/attachments/<그룹>/<분류경로>/<업무명>/


def attach_base(group: str | int, category: str) -> Path:
    """첨부 기본 폴더(업무명 제외): data/work/attachments/<그룹>/<분류경로>"""
    p = work_root() / ATTACH_ROOT / sanitize_component(str(group), "1")
    for part in cat_relpath(category).split("/"):
        p = p / part
    return p


def attach_dir(group: str | int, category: str, title: str) -> Path:
    """업무 첨부 폴더: work/attach/<그룹>/<분류경로>/<업무명>"""
    return attach_base(group, category) / sheet_file_stem(title)


def rename_attachment_dir(group: str | int, category: str, old_title: str, new_title: str):
    """업무 이름 변경 시 첨부폴더도 함께 rename. (이전경로, 새경로) 반환."""
    base = attach_base(group, category)
    old = base / sheet_file_stem(old_title)
    if not old.is_dir():
        return None
    new = unique_path(base / sheet_file_stem(new_title))
    if new.resolve() != old.resolve():
        try:
            old.rename(new)
        except Exception:
            return None
    return (old, new)


def move_dir(old_dir: Path, new_dir: Path):
    """폴더(하위 포함)를 이동/이름변경. (이전경로, 새경로) 반환. 없거나 동일하면 None."""
    if not old_dir.is_dir() or old_dir.resolve() == new_dir.resolve():
        return None
    new_dir.parent.mkdir(parents=True, exist_ok=True)
    if new_dir.exists():
        new_dir = unique_path(new_dir)
    old_dir.rename(new_dir)
    return (old_dir, new_dir)


def move_sheet_files(grp_old, old_cat, grp_new, new_cat, title):
    """업무의 본문 파일 + 첨부폴더를 새 분류로 이동. (이전경로, 새경로) 목록 반환."""
    moves = []
    stem = sheet_file_stem(title)
    raw_title = (title or "").strip()
    old_dir = category_dir(grp_old, old_cat)
    new_dir = category_dir(grp_new, new_cat)
    if old_dir.is_dir():
        new_dir.mkdir(parents=True, exist_ok=True)
        # glob() 대신 iterdir() 사용: 대괄호([제출서류] 등)가 와일드카드 패턴으로 해석되는 치명적 버그 방지
        for src in list(old_dir.iterdir()):
            if src.is_file() and (src.stem == stem or src.stem == raw_title or src.name == raw_title):
                dest = new_dir / src.name
                if dest.resolve() == src.resolve():
                    continue
                if dest.exists():
                    try:
                        dest.unlink()
                    except Exception:
                        dest = unique_path(dest)
                try:
                    src.rename(dest)
                    moves.append((src, dest))
                except Exception:
                    pass
        # 이전 분류 폴더가 비어 있으면 상위로 정리
        try:
            grp_work_root = work_root() / sanitize_component(str(grp_old), "1")
            prune_empty_parent_dirs(old_dir, grp_work_root)
        except Exception:
            pass

    old_att = attach_dir(grp_old, old_cat, title)
    if old_att.is_dir():
        new_att = attach_dir(grp_new, new_cat, title)
        new_att.parent.mkdir(parents=True, exist_ok=True)
        if new_att.exists() and new_att.resolve() != old_att.resolve():
            try:
                import shutil
                shutil.rmtree(new_att, ignore_errors=True)
            except Exception:
                new_att = unique_path(new_att)
        try:
            old_att.rename(new_att)
            moves.append((old_att, new_att))
        except Exception:
            pass
        try:
            att_root = work_root() / ATTACH_ROOT / sanitize_component(str(grp_old), "1")
            prune_empty_parent_dirs(old_att.parent, att_root)
        except Exception:
            pass
    return moves


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def scan_attach_tree(base: Path) -> list[dict]:
    """
    첨부 폴더를 재귀 스캔해 항목(폴더/파일) 목록을 만든다(폴더가 진실).
    각 항목: {name, path, size, file_type, folder_path, type}
    """
    out: list[dict] = []

    def walk(d: Path, rel: str) -> None:
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except Exception:
            return
        for p in entries:
            try:
                if p.is_dir():
                    out.append({"name": p.name, "path": str(p), "size": "", "file_type": "",
                                "folder_path": rel, "type": "folder"})
                    walk(p, f"{rel}/{p.name}".strip("/"))
                elif p.is_file():
                    out.append({"name": p.name, "path": str(p), "size": _fmt_size(p.stat().st_size),
                                "file_type": p.suffix.lower(), "folder_path": rel, "type": "file"})
            except Exception:
                continue

    if base.is_dir():
        walk(base, "")
    return out


def is_native_file(path: Path | str) -> bool:
    """한글 원본(.hwp/.hwpx) 여부 — 에디터가 그대로 로드 가능"""
    return Path(path).suffix.lower() in NATIVE_EXTENSIONS


def is_text_file(path: Path | str) -> bool:
    """텍스트 문서(txt/md 등) 여부 — 에디터로 열고 한글로 변환 가능"""
    return Path(path).suffix.lower() in TEXT_EXTENSIONS


def cat_relpath(category: str) -> str:
    """분류를 '상위/하위' 상대경로로 변환(각 조각 정제). 예: '민원/접수·처리'"""
    parts = [p for p in str(category or "").replace("\\", "/").split("/") if p.strip()]
    if not parts:
        return sanitize_component("미분류", "미분류")
    return "/".join(sanitize_component(p, "미분류") for p in parts)


def category_dir(group: str | int, category: str) -> Path:
    """work/<그룹>/<상위분류>/<분류> 폴더 경로 (분류는 상위 폴더까지 중첩)"""
    p = work_root() / sanitize_component(str(group), "1")
    for part in cat_relpath(category).split("/"):
        p = p / part
    return p


def is_editor_openable(path: Path | str) -> bool:
    """내장 HWP 에디터로 열 수 있는 확장자인지"""
    return Path(path).suffix.lower() in EDITOR_EXTENSIONS


def find_sheet_file(group: str | int, category: str, title: str) -> Path | None:
    """work/<그룹>/<분류>/<제목>.<ext> 업무 파일을 찾는다(에디터 포맷 우선)."""
    d = category_dir(group, category)
    target_dirs = [d] if d.is_dir() else []
    if not target_dirs:
        # 번호 접두사 유무 차이로 인한 폴더 탐색 (예: "1.인사" vs "인사")
        grp_dir = work_root() / sanitize_component(str(group), "1")
        if grp_dir.is_dir():
            cat_clean = strip_category_prefix(category)
            for sub_p in grp_dir.iterdir():
                if sub_p.is_dir() and strip_category_prefix(sub_p.name) == cat_clean:
                    target_dirs.append(sub_p)

    if not target_dirs:
        return None

    stem = sheet_file_stem(title)
    raw_title = (title or "").strip()
    for cur_d in target_dirs:
        cands = [
            p for p in cur_d.iterdir()
            if p.is_file() and (p.stem == stem or p.name == raw_title or p.stem == raw_title)
        ]
        if not cands:
            continue
        for group_exts in (NATIVE_EXTENSIONS, TEXT_EXTENSIONS):
            for p in cands:
                if p.suffix.lower() in group_exts:
                    return p
        return sorted(cands, key=lambda p: p.name)[0]
    return None


def rename_sheet_file(group: str | int, category: str, old_title: str, new_title: str) -> Path | None:
    """제목 변경 시 해당 업무 파일명도 함께 변경한다."""
    f = find_sheet_file(group, category, old_title)
    if not f:
        return None
    new = unique_path(category_dir(group, category) / f"{sheet_file_stem(new_title)}{f.suffix}")
    if new.resolve() != f.resolve():
        try:
            f.rename(new)
        except Exception:
            return None
    return new


def prune_empty_parent_dirs(path: Path, stop_at: Path) -> None:
    """비어 있는 상위 디렉토리를 stop_at 직전까지 거슬러 올라가며 순차 삭제한다."""
    try:
        curr = path if path.is_dir() else path.parent
        stop_resolved = stop_at.resolve()
        while curr and curr.resolve() != stop_resolved and stop_resolved in curr.resolve().parents:
            if curr.is_dir() and not any(curr.iterdir()):
                curr.rmdir()
                curr = curr.parent
            else:
                break
    except Exception:
        pass


def delete_sheet_file(group: str | int, category: str, title: str) -> bool:
    """업무 삭제 시 work/ 트리의 해당 본문 파일과 첨부 디렉토리를 물리 삭제하고, 빈 상위 폴더도 정리한다."""
    deleted = False
    f = find_sheet_file(group, category, title)
    grp_str = sanitize_component(str(group), "1")
    cat_dir = category_dir(grp_str, category)
    grp_work_root = work_root() / grp_str

    if f and f.is_file():
        try:
            f.unlink()
            deleted = True
            # 본문 파일 삭제 후 해당 분류 폴더가 비어 있으면 상위로 정리
            prune_empty_parent_dirs(f.parent, grp_work_root)
        except Exception:
            pass
    elif cat_dir.is_dir():
        # 혹시 본문이 폴더 형태였거나 파일이 이미 지워졌더라도 빈 부모 정리
        prune_empty_parent_dirs(cat_dir, grp_work_root)

    # 첨부 디렉토리(work/attach/<그룹>/<분류>/<업무명>) 및 동일 이름 폴더 삭제
    att_root = work_root() / ATTACH_ROOT / grp_str
    att = attach_dir(group, category, title)
    target_dirs = [att]
    stem = sheet_file_stem(title)
    base = attach_base(group, category)
    if base.is_dir():
        for d in (base / stem, base / title.strip()):
            if d not in target_dirs:
                target_dirs.append(d)

    for d in target_dirs:
        if d.is_dir():
            try:
                shutil.rmtree(d, ignore_errors=True)
                deleted = True
                prune_empty_parent_dirs(d.parent, att_root)
            except Exception:
                pass
        elif d.parent.is_dir():
            prune_empty_parent_dirs(d.parent, att_root)

    return deleted


def delete_category_dir(group: str | int, category: str) -> bool:
    """분류(폴더) 삭제 시 work/<그룹>/<분류> 폴더 및 work/attach/<그룹>/<분류> 폴더를 물리 삭제하고 빈 상위 폴더를 정리한다."""
    deleted = False
    grp_str = sanitize_component(str(group), "1")
    grp_work_root = work_root() / grp_str
    cdir = category_dir(grp_str, category)
    if cdir.is_dir():
        try:
            shutil.rmtree(cdir, ignore_errors=True)
            deleted = True
            prune_empty_parent_dirs(cdir.parent, grp_work_root)
        except Exception:
            pass

    att_root = work_root() / ATTACH_ROOT / grp_str
    abool = attach_base(grp_str, category)
    if abool.is_dir():
        try:
            shutil.rmtree(abool, ignore_errors=True)
            deleted = True
            prune_empty_parent_dirs(abool.parent, att_root)
        except Exception:
            pass
    return deleted


def open_path_with_os(path: Path | str) -> bool:
    """OS 기본 연결 프로그램으로 파일을 연다(한글/엑셀 등)."""
    p = str(Path(path))
    if sys.platform == "win32":
        try:
            os.startfile(p)  # type: ignore[attr-defined]
            return True
        except Exception:
            pass
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
    except Exception:
        return False


def reveal_in_explorer(path: Path | str) -> bool:
    """탐색기에서 해당 파일을 선택 표시한다."""
    p = Path(path)
    if sys.platform == "win32":
        try:
            subprocess.Popen(f'explorer /select,"{p}"')
            return True
        except Exception:
            pass
    return open_path_with_os(p.parent)


def build_empty_hwpx(title: str = "새 업무") -> bytes:
    """한글(HWP) 및 웹 에디터 호환용 표준 빈 HWPX 바이너리를 즉시 생성한다."""
    import io
    from taskcalendar.korean_report_hwpx.engine import blank

    hdr = blank.header_xml()
    sec_p = blank.sec_pr({})
    t = (title or "새 업무").strip()
    first_p = (
        f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0">'
        f'<hp:run charPrIDRef="0">{sec_p}<hp:t>{t}</hp:t></hp:run></hp:p>'
    )
    sec = blank.section_xml([first_p])
    buf = io.BytesIO()
    blank.save(buf, hdr, sec, title=t, preview=t)
    return buf.getvalue()


def create_empty_hwpx_file(dest_path: Path | str, title: str = "새 업무") -> Path:
    """dest_path 경로에 빈 HWPX 파일을 생성하고 경로를 반환한다."""
    dest = Path(dest_path)
    if dest.suffix.lower() != ".hwpx":
        dest = dest.with_suffix(".hwpx")
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = build_empty_hwpx(title or dest.stem)
    dest.write_bytes(blob)
    return dest
