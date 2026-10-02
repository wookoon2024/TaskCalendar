import sys
from taskcalendar.app import run


def _ensure_assets() -> None:
    if not getattr(sys, "frozen", False):
        try:
            from restore import restore_app_icon, restore_rhwp_wasm, restore_chrome_extension_zip
            restore_app_icon()
            restore_rhwp_wasm()
            restore_chrome_extension_zip()
        except Exception:
            pass


if __name__ == "__main__":
    _ensure_assets()
    run()
