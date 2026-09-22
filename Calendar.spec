# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('taskcalendar/assets', 'taskcalendar/assets'), ('data/holidays_kr.json', 'data')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'tcl', 'numpy', 'scipy', 'matplotlib', 'PIL', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtVirtualKeyboard', 'PySide6.QtPdf', 'PySide6.QtOpenGL', 'lxml', 'bs4', 'soupsieve', 'chardet', 'charset_normalizer'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('O', None, 'OPTION')],
    name='Calendar',
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
    icon=['taskcalendar\\assets\\app_icon.ico'],
)
