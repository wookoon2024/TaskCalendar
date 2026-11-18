# -*- mode: python ; coding: utf-8 -*-
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
    runtime_tmpdir=r'%LOCALAPPDATA%\TaskCalendar\runtime',  # %TEMP% 대신 (부팅 직후 보안 프로그램의 TEMP 차단 회피)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['taskcalendar\\assets\\app_icon.ico'],
)
