"""
Build script for TaskCalendar Windows executable using PyInstaller.
Can be executed directly on intranet machines:
    python build_exe.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from restore import restore_app_icon, restore_rhwp_wasm, restore_chrome_extension_zip

ROOT = Path(__file__).resolve().parent

SPEC_TEMPLATE = '''# -*- mode: python ; coding: utf-8 -*-
try:
    from restore import restore_app_icon, restore_rhwp_wasm, restore_chrome_extension_zip
    restore_app_icon()
    restore_rhwp_wasm()
    restore_chrome_extension_zip()
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('taskcalendar/assets', 'taskcalendar/assets'), ('data/holidays_kr.json', 'data'), ('chrome_extension', 'chrome_extension'), ('업무 템플릿', '업무 템플릿')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'tcl', 'numpy', 'scipy', 'matplotlib', 'PIL', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtVirtualKeyboard', 'PySide6.QtPdf', 'PySide6.QtOpenGL', 'lxml', 'bs4', 'soupsieve'],
    noarchive=False,
    optimize=1,
)

_unneeded_binaries = {
    'opengl32sw.dll',
    'qtvirtualkeyboardplugin.dll',
    'qpdf.dll',
    'qtga.dll',
    'qtiff.dll',
    'qwbmp.dll',
}
a.binaries = [x for x in a.binaries if not any(ub in x[0].lower() for ub in _unneeded_binaries)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('O', None, 'OPTION')],
    name='나라수첩',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['taskcalendar\\\\assets\\\\app_icon.ico'],
)
'''


def main() -> None:
    os.chdir(ROOT)

    print("=== Step 1: Restoring assets ===")
    restore_app_icon()
    restore_rhwp_wasm()
    restore_chrome_extension_zip()

    print("=== Step 2: Preparing spec file ===")
    spec_path = ROOT / "Calendar.spec"
    if not spec_path.exists():
        spec_path.write_text(SPEC_TEMPLATE, encoding="utf-8")
        print("[OK] Generated Calendar.spec")

    import subprocess
    cmd = [sys.executable, "-m", "PyInstaller", str(spec_path), "--noconfirm", "--clean"]
    print(f"Executing: {' '.join(cmd)}")
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n=== Build Completed Successfully! ===")
        dist_dir = ROOT / "dist"
        dist_exe = dist_dir / "나라수첩.exe"
        if dist_exe.exists():
            print(f"Output executable: {dist_exe} ({dist_exe.stat().st_size:,} bytes)")
        ext_zip = ROOT / "chrome_extension.zip"
        if ext_zip.exists():
            import shutil
            shutil.copy2(ext_zip, dist_dir / "chrome_extension.zip")
            print(f"Output extension: {dist_dir / 'chrome_extension.zip'} ({ext_zip.stat().st_size:,} bytes)")
    else:
        print(f"\n[ERROR] PyInstaller failed with exit code {res.returncode}")
        sys.exit(res.returncode)


if __name__ == "__main__":
    main()
