"""
Restores required binary assets (app_icon.ico, rhwp wasm) from safe files.
Useful when files were transferred across network security filters where .ico / .wasm are blocked.
"""
from __future__ import annotations

import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def restore_app_icon() -> bool:
    ico_path = ROOT / "taskcalendar" / "assets" / "app_icon.ico"
    png_path = ROOT / "taskcalendar" / "assets" / "app_icon.png"
    if png_path.exists() and (not ico_path.exists() or ico_path.stat().st_size == 0):
        png_data = png_path.read_bytes()
        header = struct.pack("<HHH", 0, 1, 1)
        entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_data), 22)
        ico_path.write_bytes(header + entry + png_data)
        print(f"[OK] Generated {ico_path.relative_to(ROOT)} from PNG")
        return True
    return False


def restore_rhwp_wasm() -> bool:
    assets_dir = ROOT / "taskcalendar" / "assets" / "rhwp" / "studio" / "assets"
    dat_path = assets_dir / "rhwp_bg-PUGAA2uC.dat"
    wasm_path = assets_dir / "rhwp_bg-PUGAA2uC.wasm"
    if dat_path.exists() and (not wasm_path.exists() or wasm_path.stat().st_size == 0):
        shutil.copy2(dat_path, wasm_path)
        print(f"[OK] Restored {wasm_path.relative_to(ROOT)} from DAT")
        return True
    return False


def main() -> None:
    print("Checking and restoring network-filtered assets...")
    restore_app_icon()
    restore_rhwp_wasm()
    print("Asset restoration complete.")


if __name__ == "__main__":
    main()
