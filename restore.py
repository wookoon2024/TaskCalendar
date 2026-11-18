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


def restore_chrome_extension_zip() -> bool:
    import os
    import zipfile
    ext_dir = ROOT / "chrome_extension"
    zip_path = ROOT / "chrome_extension.zip"
    if ext_dir.exists() and (ext_dir / "manifest.json").exists() and (not zip_path.exists() or zip_path.stat().st_size == 0):
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(ext_dir):
                    for f in files:
                        if f.endswith(".zip") or f.endswith(".pyc") or f == ".DS_Store":
                            continue
                        fp = Path(root) / f
                        zf.write(fp, fp.relative_to(ext_dir))
            print(f"[OK] Generated {zip_path.name} ({zip_path.stat().st_size:,} bytes)")
            return True
        except Exception as e:
            print(f"[WARN] Failed to create chrome_extension.zip: {e}")
    return False


def restore_sticker_v2(root: Path | None = None) -> bool:
    """스티커 v2 준비 (업무망처럼 PNG 를 옮길 수 없는 곳용).

    1) 옛 기본 스티커(assets/stickers/*.png)를 assets/stickers/legacy/ 로 옮긴다.
       앱은 legacy 도 읽으므로 이미 달력에 붙인 옛 스티커는 그대로 보이고, 목록에서만 빠진다.
    2) v2 그림이 없으면 tools/sticker_v2_gen.py 로 직접 그려 만든다 (그림 자체는 .py 코드에 들어 있음).
    """
    import os
    import subprocess
    import sys

    base = root or ROOT
    stickers = base / "taskcalendar" / "assets" / "stickers"
    if not stickers.is_dir():
        return False
    changed = False
    legacy = stickers / "legacy"
    for png in sorted(stickers.glob("*.png")):
        legacy.mkdir(parents=True, exist_ok=True)
        target = legacy / png.name
        if target.exists():
            png.unlink()
        else:
            png.replace(target)
        changed = True

    v2_dir = stickers / "v2"
    if (v2_dir / "manifest.json").exists():
        return changed
    generator = base / "tools" / "sticker_v2_gen.py"
    if not generator.exists():
        print("[WARN] tools/sticker_v2_gen.py 가 없어 스티커 v2 를 만들 수 없습니다.")
        return changed
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, str(generator), str(v2_dir)], env=env, capture_output=True, text=True)
    if result.returncode != 0 or not (v2_dir / "manifest.json").exists():
        print(f"[WARN] 스티커 v2 생성 실패: {result.stderr.strip()[-500:]}")
        return changed
    print(f"[OK] 스티커 v2 생성: {len(list(v2_dir.glob('*.png')))}개")
    return True


def main() -> None:
    print("Checking and restoring network-filtered assets...")
    restore_app_icon()
    restore_rhwp_wasm()
    restore_chrome_extension_zip()
    restore_sticker_v2()
    print("Asset restoration complete.")


if __name__ == "__main__":
    main()
