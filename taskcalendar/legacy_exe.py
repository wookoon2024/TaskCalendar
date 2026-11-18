"""예전 실행 파일(바로업무.exe) 정리.

v2.2.0 부터 실행 파일 이름이 calendar.exe 로 바뀌었다 (브라우저 taskcalendar:// 연동이 한글 파일명에서 동작하지 않아서).
새 exe 를 같은 폴더에 두면 예전 바로업무.exe 가 그대로 남는데, 자료를 data/ 로 옮긴 뒤 예전 exe 를 실행하면
빈 DB 로 시작해 자료가 두 곳으로 나뉜다. 그래서 calendar.exe 가 실행될 때:

1) 같은 폴더의 바로업무.exe 를 바로업무.exe.old 로 이름을 바꿔 실행되지 않게 한다 (지우지 않음 → 필요하면 되돌릴 수 있음)
2) 바탕화면·시작 메뉴·작업 표시줄에서 그 바로업무.exe 를 가리키는 바로가기를 calendar.exe 로 바꾼다
   (다른 폴더의 바로업무.exe 를 가리키는 바로가기는 그 폴더의 자료를 쓰는 것이므로 건드리지 않는다)
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LEGACY_EXE_NAMES = ("바로업무.exe",)
_LEGACY_MARKERS = tuple(
    marker
    for name in LEGACY_EXE_NAMES
    for marker in (Path(name).stem.encode("utf-16-le"), Path(name).stem.encode("utf-8"), Path(name).stem.encode("cp949"))
)


def _known_desktop_dir() -> Path | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        ) as key:
            raw, _ = winreg.QueryValueEx(key, "Desktop")
            return Path(os.path.expandvars(str(raw)))
    except Exception:
        return None


def default_shortcut_dirs() -> list[tuple[Path, bool]]:
    """(폴더, 하위 폴더까지 찾을지) 목록: 바탕화면, 시작 메뉴, 작업 표시줄 고정."""
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    programdata = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    public = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
    dirs: list[tuple[Path, bool]] = []
    desktop = _known_desktop_dir()
    if desktop is not None:
        dirs.append((desktop, False))
    dirs += [
        (Path.home() / "Desktop", False),
        (public / "Desktop", False),
        (appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs", True),
        (programdata / "Microsoft" / "Windows" / "Start Menu" / "Programs", True),
        (appdata / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar", False),
        (appdata / "Microsoft" / "Internet Explorer" / "Quick Launch", False),
    ]
    seen: set[str] = set()
    unique: list[tuple[Path, bool]] = []
    for d, recursive in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            unique.append((d, recursive))
    return unique


def _candidate_shortcuts(dirs: list[tuple[Path, bool]]) -> list[Path]:
    """바로업무 이름이 들어 있는 .lnk 만 고른다 (모든 바로가기를 PowerShell 로 열지 않도록)."""
    found: list[Path] = []
    for folder, recursive in dirs:
        try:
            if not folder.is_dir():
                continue
            files = folder.rglob("*.lnk") if recursive else folder.glob("*.lnk")
            for lnk in files:
                try:
                    data = lnk.read_bytes()
                except OSError:
                    continue
                if any(marker in data for marker in _LEGACY_MARKERS):
                    found.append(lnk)
        except OSError:
            continue
    return found


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _retarget_shortcuts(shortcuts: list[Path], old_targets: list[Path], new_target: Path) -> list[str]:
    """WScript.Shell 로 바로가기 대상이 old_targets 중 하나면 new_target 으로 바꾼다. 바꾼 바로가기 경로 목록을 돌려준다."""
    if not shortcuts or not old_targets:
        return []
    script = "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "$sh = New-Object -ComObject WScript.Shell",
        "$old = @(" + ",".join(_ps_quote(str(p)) for p in old_targets) + ")",
        "$new = " + _ps_quote(str(new_target)),
        "$dir = " + _ps_quote(str(new_target.parent)),
        "foreach ($p in @(" + ",".join(_ps_quote(str(p)) for p in shortcuts) + ")) {",
        "  try {",
        "    $s = $sh.CreateShortcut($p)",
        "    if ($old -contains $s.TargetPath) {",
        "      $s.TargetPath = $new; $s.WorkingDirectory = $dir; $s.IconLocation = \"$new,0\"; $s.Save()",
        "      Write-Output (\"UPDATED`t\" + $p)",
        "    }",
        "  } catch {}",
        "}",
    ])
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        logger.warning("legacy shortcut retarget failed: %s", exc)
        return []
    out = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    return [line.split("\t", 1)[1].strip() for line in out.splitlines() if line.startswith("UPDATED\t")]


def retire_legacy_exe(current_exe: Path, shortcut_dirs: list[tuple[Path, bool]] | None = None) -> dict:
    """예전 실행 파일 이름 바꾸기 + 바로가기 대상 바꾸기. 결과를 dict 로 돌려준다 (로그용)."""
    current_exe = Path(current_exe)
    folder = current_exe.parent
    renamed: list[str] = []
    old_targets: list[Path] = []
    for name in LEGACY_EXE_NAMES:
        old = folder / name
        old_targets.append(old)
        try:
            if not old.exists() or old.resolve() == current_exe.resolve():
                continue
            target = old.with_name(name + ".old")
            if target.exists():
                target = old.with_name(f"{name}.{datetime.now():%Y%m%d%H%M%S}.old")
            old.rename(target)
            renamed.append(str(target))
            logger.info("Retired legacy exe: %s -> %s", old, target)
        except OSError as exc:
            logger.warning("Could not retire legacy exe %s: %s", old, exc)

    shortcuts = _candidate_shortcuts(shortcut_dirs if shortcut_dirs is not None else default_shortcut_dirs())
    updated = _retarget_shortcuts(shortcuts, old_targets, current_exe)
    for path in updated:
        logger.info("Retargeted shortcut to %s: %s", current_exe.name, path)
    return {"renamed": renamed, "shortcuts": updated}


def retire_legacy_exe_in_background() -> None:
    """빌드된 exe 로 실행 중일 때만, 시작을 늦추지 않도록 별도 스레드에서 정리한다."""
    import sys
    import threading

    if not getattr(sys, "frozen", False):
        return
    exe = Path(sys.executable)
    if exe.name in LEGACY_EXE_NAMES:
        return  # 예전 이름 그대로 빌드된 경우에는 아무것도 하지 않는다

    def _run() -> None:
        try:
            retire_legacy_exe(exe)
        except Exception:
            logger.exception("legacy exe cleanup failed")

    threading.Thread(target=_run, name="legacy-exe-cleanup", daemon=True).start()
