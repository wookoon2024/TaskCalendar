# -*- coding: utf-8 -*-
from pathlib import Path

p = Path("Calendar.spec")
if not p.exists():
    print("Calendar.spec 파일을 찾을 수 없습니다.")
    exit(1)

text = p.read_text(encoding="utf-8")
target = "'PySide6.QtOpenGL',"
replacement = "'PySide6.QtOpenGL',\n        'lxml', 'bs4', 'soupsieve', 'chardet', 'charset_normalizer',"

if "soupsieve" in text:
    print("이미 Calendar.spec에 취약 라이브러리 제외 설정이 적용되어 있습니다!")
elif target in text:
    p.write_text(text.replace(target, replacement), encoding="utf-8")
    print("Calendar.spec 수정이 완료되었습니다!")
else:
    print("대상을 찾지 못했습니다. 메모장으로 excludes에 직접 추가해주세요.")
