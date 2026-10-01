"""TaskCalendar package."""

import os
import sys

if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    try:
        import importlib.util

        spec = importlib.util.find_spec("PySide6")
        if spec and spec.submodule_search_locations:
            for p in spec.submodule_search_locations:
                if os.path.isdir(p):
                    os.add_dll_directory(p)
    except Exception:
        pass

__version__ = "v1.9.9"
APP_VERSION = __version__
