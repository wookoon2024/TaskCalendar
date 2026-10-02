"""
Restores required binary assets (app_icon.ico, rhwp wasm) from safe files.
Useful when files were transferred across network security filters where .ico / .wasm are blocked.
"""
from __future__ import annotations

import struct
import zlib
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
    wasm_path = assets_dir / "rhwp_bg-PUGAA2uC.wasm"
    png_path = assets_dir / "rhwp_engine.png"
    if png_path.exists() and (not wasm_path.exists() or wasm_path.stat().st_size == 0):
        data = png_path.read_bytes()
        idx = 8
        idat_acc = bytearray()
        w, h = 0, 0
        while idx < len(data):
            clen = struct.unpack(">I", data[idx : idx + 4])[0]
            ctype = data[idx + 4 : idx + 8]
            cdata = data[idx + 8 : idx + 8 + clen]
            if ctype == b"IHDR":
                w, h = struct.unpack(">II", cdata[:8])
            elif ctype == b"IDAT":
                idat_acc.extend(cdata)
            idx += 12 + clen
        decomp = zlib.decompress(bytes(idat_acc))
        rb = w * 4
        payload = bytearray()
        for i in range(h):
            start = i * (rb + 1) + 1
            payload.extend(decomp[start : start + rb])
        olen = struct.unpack(">I", payload[:4])[0]
        wasm_bytes = bytes(payload[4 : 4 + olen])
        wasm_path.write_bytes(wasm_bytes)
        print(f"[OK] Restored {wasm_path.relative_to(ROOT)} from {png_path.name} ({len(wasm_bytes):,} bytes)")
        return True
    return False


def main() -> None:
    print("Checking and restoring network-filtered assets...")
    restore_app_icon()
    restore_rhwp_wasm()
    print("Asset restoration complete.")


if __name__ == "__main__":
    main()
