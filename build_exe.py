"""
Build script for TaskCalendar Windows executable using PyInstaller.
Can be executed directly on intranet machines:
    python build_exe.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from restore import restore_app_icon, restore_rhwp_wasm, restore_chrome_extension_zip, restore_sticker_v2

ROOT = Path(__file__).resolve().parent

SPEC_TEMPLATE = '''# -*- mode: python ; coding: utf-8 -*-
try:
    from restore import restore_app_icon, restore_rhwp_wasm, restore_chrome_extension_zip, restore_sticker_v2
    restore_app_icon()
    restore_rhwp_wasm()
    restore_chrome_extension_zip()
    restore_sticker_v2()
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('taskcalendar/assets', 'taskcalendar/assets'), ('taskcalendar/korean_report_hwpx', 'taskcalendar/korean_report_hwpx'), ('data/holidays_kr.json', 'data'), ('chrome_extension', 'chrome_extension')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'tcl', 'numpy', 'scipy', 'matplotlib', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtVirtualKeyboard', 'PySide6.QtPdf', 'PySide6.QtOpenGL', 'lxml', 'bs4', 'soupsieve'],
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
# QtQuick/QML 은 사용하지 않는다. PySide6 버전에 따라 qml 폴더(수천 개 파일)가 딸려 들어와
# onefile 실행 시 압축 해제 실패(Failed to extract entry ... qml)의 원인이 되므로 명시적으로 뺀다.
a.datas = [x for x in a.datas if not x[0].replace(chr(92), '/').lower().startswith('pyside6/qml/')]
a.binaries = [x for x in a.binaries if not x[0].replace(chr(92), '/').lower().startswith('pyside6/qml/')]
# 크롬 개발자도구(devtools)·디버그용 WebEngine 리소스 (~97MB) 제외 — 문서 편집기는 이 파일 없이 정상 동작한다.
# onefile 은 실행할 때마다 전부 풀기 때문에, 빼면 시작이 빨라지고 압축 해제 실패 가능성도 준다.
a.datas = [x for x in a.datas if not (
    x[0].replace(chr(92), '/').lower().startswith('pyside6/resources/')
    and ('devtools' in x[0].lower() or '.debug.' in x[0].lower())
)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('O', None, 'OPTION')],
    # 브라우저 연동(taskcalendar:// 프로토콜)이 한글 파일명에서 동작하지 않아 영문 이름으로 빌드한다.
    name='calendar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=r'%LOCALAPPDATA%\\TaskCalendar\\runtime',  # %TEMP% 대신 (부팅 직후 보안 프로그램의 TEMP 차단 회피)
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
    restore_sticker_v2()

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
        cal_exe = dist_dir / "calendar.exe"
        import shutil
        if cal_exe.exists():
            print(f"Output executable: {cal_exe} ({cal_exe.stat().st_size:,} bytes)")
        ext_zip = ROOT / "chrome_extension.zip"
        if ext_zip.exists():
            shutil.copy2(ext_zip, dist_dir / "chrome_extension.zip")
            print(f"Output extension: {dist_dir / 'chrome_extension.zip'} ({ext_zip.stat().st_size:,} bytes)")
    else:
        print(f"\n[ERROR] PyInstaller failed with exit code {res.returncode}")
        sys.exit(res.returncode)


if __name__ == "__main__":
    main()
