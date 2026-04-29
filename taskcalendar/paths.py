from __future__ import annotations

import os
import sys
from pathlib import Path


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        argv0 = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else Path(sys.executable).resolve()
        candidate = argv0.parent
        lowered = str(candidate).lower()
        # In some onefile launch paths, the process can run from a temporary extraction directory.
        # Persist user data under LOCALAPPDATA instead of a volatile temp location.
        if "\\appdata\\local\\temp\\" in lowered or "\\temp\\" in lowered or "_mei" in lowered or "\\bnz." in lowered:
            local_appdata = Path(os.environ.get("LOCALAPPDATA", str(candidate)))
            return local_appdata / "TaskCalendar"
        return candidate
    return Path(__file__).resolve().parent.parent


def package_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass) / "taskcalendar"
    return Path(__file__).resolve().parent


def asset_path(*parts: str) -> Path:
    return package_root() / "assets" / Path(*parts)


def data_path(*parts: str) -> Path:
    return runtime_root() / "data" / Path(*parts)


def logs_path(*parts: str) -> Path:
    return runtime_root() / "logs" / Path(*parts)
