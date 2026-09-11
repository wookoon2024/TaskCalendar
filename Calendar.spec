# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('taskcalendar/assets', 'taskcalendar/assets'), ('data/holidays_kr.json', 'data')],
    hiddenimports=['korean_lunar_calendar'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', '_tkinter', 'tcl',
        'numpy', 'scipy', 'matplotlib', 'PIL',
        'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtVirtualKeyboard',
        'PySide6.QtPdf', 'PySide6.QtOpenGL',
    ],
    noarchive=False,
    optimize=1,
)

# Filter out heavy unneeded Qt binaries (e.g. software OpenGL renderer ~20MB, virtual keyboard, etc.)
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
    [],
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
