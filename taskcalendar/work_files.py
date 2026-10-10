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


def work_root() -> Path:
    """업무 파일 루트 (데이터 루트 기준: <데이터루트>/work)"""
    from taskcalendar.paths import runtime_root

    return runtime_root() / "work"


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
    for ext in (".hwpx", ".hwp"):
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


_ATTACH_SUFFIX = "_첨부파일"


def file_sha1(path: Path | str) -> str:
    """파일 내용 해시(rename 자동 인식용)"""
    import hashlib

    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_work_files(known_categories: set | None = None) -> list[dict]:
    """
    work/ 트리에서 업무 항목(파일·폴더) 목록을 스캔한다.
    - '*_첨부파일' 폴더 내부는 제외
    - 폴더는 '업무 폴더'로 간주: 알려진 분류명이 아니고 깊이 3 이상일 때만
    각 항목: {path, group, category, stem, ext, size, sha1, is_dir}
    """
    root = work_root()
    out: list[dict] = []
    if not root.is_dir():
        return out
    known = {c for c in (known_categories or set())}
    for p in root.rglob("*"):
        rel_parts = p.relative_to(root).parts
        if rel_parts and rel_parts[0] == ATTACH_ROOT:
            continue  # 첨부 전용 트리는 업무 항목이 아님
        group = rel_parts[0] if len(rel_parts) >= 3 else "1"
        try:
            cat = p.parent.relative_to(root / group).as_posix()
        except Exception:
            cat = p.parent.name
        try:
            if p.is_file():
                out.append({
                    "path": p, "group": group, "category": cat, "stem": p.stem,
                    "ext": p.suffix.lower(), "size": p.stat().st_size, "sha1": file_sha1(p), "is_dir": False,
                })
            elif p.is_dir() and len(rel_parts) >= 3 and p.name not in known:
                out.append({
                    "path": p, "group": group, "category": cat, "stem": p.name,
                    "ext": "", "size": 0, "sha1": "", "is_dir": True,
                })
        except Exception:
            continue
    # 같은 업무에 원본(.hwp/.hwpx)과 텍스트 사본이 함께 있으면 텍스트는 목록에서 제외
    native_keys = {
        (f["group"], f["category"], f["stem"])
        for f in out
        if (not f["is_dir"]) and f["ext"] in NATIVE_EXTENSIONS
    }
    out = [
        f for f in out
        if f["is_dir"] or f["ext"] in NATIVE_EXTENSIONS
        or (f["group"], f["category"], f["stem"]) not in native_keys
    ]
    return out


def plan_reconcile(db_items: list[dict], disk_files: list[dict]) -> dict:
    """
    DB 업무문서와 work/ 파일을 비교한다.
    db_items: [{"id","title","category_name","sha1"}] (sha1 = blob 해시)
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
        want_grp = sanitize_component(str(db.get("group") or "1"), "1")
        hit = next(
            (f for f in disk_files if id(f) not in used and f.get("group") == want_grp
             and f["category"] == want_cat and f["stem"] == want_stem),
            None,
        )
        if hit:
            used.add(id(hit))
            matched.append((db, hit))
        else:
            missing_candidates.append(db)

    renamed_db: set[int] = set()
    for db in missing_candidates:
        sha = db.get("sha1")
        hit = next((f for f in disk_files if id(f) not in used and sha and f["sha1"] == sha), None)
        if hit:
            used.add(id(hit))
            renamed.append((db, hit))
            renamed_db.add(id(db))

    missing = [db for db in missing_candidates if id(db) not in renamed_db]
    new = [f for f in disk_files if id(f) not in used]
    return {"matched": matched, "renamed": renamed, "missing": missing, "new": new}


NATIVE_EXTENSIONS = (".hwp", ".hwpx")          # 내장 에디터의 원본 포맷
TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".hml")  # 텍스트(에디터로 열림)
EDITOR_EXTENSIONS = NATIVE_EXTENSIONS + TEXT_EXTENSIONS


ATTACH_ROOT = "attach"  # 첨부 전용 루트: work/attach/<그룹>/<분류경로>/<업무명>/


def attach_base(group: str | int, category: str) -> Path:
    """첨부 기본 폴더(업무명 제외): work/attach/<그룹>/<분류경로>"""
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
    old_dir = category_dir(grp_old, old_cat)
    new_dir = category_dir(grp_new, new_cat)
    if old_dir.is_dir():
        new_dir.mkdir(parents=True, exist_ok=True)
        for src in list(old_dir.glob(f"{stem}.*")):
            if src.is_file():
                dest = unique_path(new_dir / src.name)
                try:
                    src.rename(dest)
                    moves.append((src, dest))
                except Exception:
                    pass
    old_att = attach_dir(grp_old, old_cat, title)
    if old_att.is_dir():
        new_att = attach_dir(grp_new, new_cat, title)
        new_att.parent.mkdir(parents=True, exist_ok=True)
        if new_att.exists():
            new_att = unique_path(new_att)
        try:
            old_att.rename(new_att)
            moves.append((old_att, new_att))
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
    if not d.is_dir():
        return None
    stem = sheet_file_stem(title)
    cands = [p for p in d.iterdir() if p.is_file() and p.stem == stem]
    if not cands:
        return None
    for group in (NATIVE_EXTENSIONS, TEXT_EXTENSIONS):
        for p in cands:
            if p.suffix.lower() in group:
                return p
    return sorted(cands, key=lambda p: p.name)[0]


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
