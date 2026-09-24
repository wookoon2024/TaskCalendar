"""앱 전역 글꼴(패밀리/크기 배율) 관리.

- 번들된 Pretendard(assets/fonts/*.otf)를 Qt 폰트 DB에 등록한다.
- 앱 글꼴 패밀리와 글자 크기 배율을 보관하고, QSS에 넣을 값과 배율 환산 함수를 제공한다.
- 위젯마다 인라인으로 지정된 `font-size`까지 전역으로 반영하기 위해
  `QWidget.setStyleSheet`를 한 번 감싸 원본 QSS를 위젯별로 기억하고 배율을 적용해 전달한다.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from taskcalendar.paths import asset_path

DEFAULT_FAMILY = "Pretendard"
BASE_FONT_PX = 13

# 배율 키 -> 배율 값 (설정에는 키를 저장한다)
SCALE_OPTIONS: dict[str, float] = {
    "small": 0.9,
    "normal": 1.0,
    "large": 1.15,
    "xlarge": 1.3,
}
DEFAULT_SCALE = "normal"

# 선택한 글꼴에 없는 글자(이모지 등)를 위한 폴백
FALLBACK_FAMILIES: tuple[str, ...] = ("Segoe UI Emoji", "Malgun Gothic", "Segoe UI")

_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*(px|pt)")
_RAW_QSS_ATTR = "_cc_raw_qss"

_family: str = DEFAULT_FAMILY
_scale: float = SCALE_OPTIONS[DEFAULT_SCALE]
_loaded_families: list[str] = []
_hook_installed = False
_original_set_style_sheet = None


def load_bundled_fonts() -> list[str]:
    """assets/fonts 아래의 폰트 파일을 등록하고 등록된 패밀리 이름을 돌려준다."""
    global _loaded_families
    fonts_dir = asset_path("fonts")
    if not fonts_dir.exists():
        return []

    families: list[str] = []
    for pattern in ("*.otf", "*.ttf"):
        for path in sorted(fonts_dir.glob(pattern)):
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id != -1:
                families.extend(QFontDatabase.applicationFontFamilies(font_id))
    _loaded_families = families
    return families


def loaded_families() -> list[str]:
    return list(_loaded_families)


def set_ui_font(family: str | None = None, scale: str | float | None = None) -> None:
    """앱 전역 글꼴 패밀리와 크기 배율을 설정한다."""
    global _family, _scale
    if family is not None:
        _family = (family or "").strip() or DEFAULT_FAMILY
    if scale is not None:
        if isinstance(scale, str):
            _scale = SCALE_OPTIONS.get(scale, SCALE_OPTIONS[DEFAULT_SCALE])
        else:
            _scale = float(scale)
        if _scale <= 0:
            _scale = 1.0

    app = QApplication.instance()
    if app is not None:
        # 앱 폰트는 포인트 크기로 지정한다. pixelSize만 지정하면 pointSize()가 -1이 되어
        # QFontComboBox 등 Qt 내부 코드가 잘못된 값을 넘기며 경고를 낸다.
        # (QSS 기준값은 px이므로 화면 DPI로 환산해 같은 크기가 되도록 맞춘다)
        screen = app.primaryScreen()
        dpi = float(screen.logicalDotsPerInch()) if screen is not None else 96.0
        if dpi <= 0:
            dpi = 96.0
        font = QFont(_family)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        font.setPointSizeF(max(1.0, scale_px(BASE_FONT_PX) * 72.0 / dpi))
        app.setFont(font)


def ui_font_family() -> str:
    return _family


def ui_font_scale() -> float:
    return _scale


def scale_key(scale: float | None = None) -> str:
    """배율 값을 설정 키로 되돌린다."""
    value = _scale if scale is None else float(scale)
    for key, candidate in SCALE_OPTIONS.items():
        if abs(candidate - value) < 1e-6:
            return key
    return DEFAULT_SCALE


def font_family_css() -> str:
    """QSS의 font-family 값. (QSS는 쉼표 구분 폴백 목록을 지원하지 않으므로 단일 패밀리 반환)"""
    return f'"{_family}"'


def scale_px(value: float) -> int:
    return max(1, round(value * _scale))


def scale_qss(qss: str) -> str:
    """QSS 문자열의 font-size 값만 배율에 맞게 환산한다."""
    if not qss or _scale == 1.0 or "font-size" not in qss:
        return qss

    def replace(match: re.Match) -> str:
        number, unit = match.group(1), match.group(2)
        scaled = float(number) * _scale
        if unit == "pt":
            text = f"{scaled:.2f}".rstrip("0").rstrip(".")
        else:
            text = str(max(1, round(scaled)))
        return f"font-size: {text}{unit}"

    try:
        return _FONT_SIZE_RE.sub(replace, qss)
    except Exception:
        return qss


def install_style_hook() -> None:
    """QWidget.setStyleSheet를 감싸 모든 인라인 스타일시트에 배율을 적용한다."""
    global _hook_installed, _original_set_style_sheet
    if _hook_installed:
        return

    _original_set_style_sheet = QWidget.setStyleSheet

    def patched(widget, qss):
        try:
            setattr(widget, _RAW_QSS_ATTR, qss)
        except Exception:
            pass
        return _original_set_style_sheet(widget, scale_qss(qss))

    QWidget.setStyleSheet = patched
    _hook_installed = True


def reapply_widget_styles() -> None:
    """이미 만들어진 위젯의 인라인 스타일시트를 현재 배율로 다시 적용한다."""
    app = QApplication.instance()
    if app is None:
        return
    setter = _original_set_style_sheet or QWidget.setStyleSheet
    for widget in app.allWidgets():
        raw = getattr(widget, _RAW_QSS_ATTR, None)
        if not raw:
            continue
        try:
            setter(widget, scale_qss(raw))
        except Exception:
            pass
