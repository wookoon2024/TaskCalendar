from __future__ import annotations

from taskcalendar.fonts import font_family_css
from taskcalendar.paths import asset_path
from taskcalendar.themes import THEMES


def resolve_palette(source) -> dict[str, str]:
    """Return a full skin palette for a dialog.

    Accepts a widget (reads its ``palette`` attribute) or a palette dict.
    Falls back to the light theme so dialogs never crash when the parent
    is ``None`` (e.g. the headless entry-dialog bridge).
    """
    palette = getattr(source, "palette", None)
    if isinstance(palette, dict) and palette.get("bg"):
        return palette
    if isinstance(source, dict) and source.get("bg"):
        return source
    return THEMES["light"]


def _shade(hex_color: str, factor: float) -> str:
    """Darken (factor < 0) or lighten (factor > 0) a #rrggbb color."""
    color = str(hex_color).lstrip("#")
    if len(color) != 6:
        return "#" + color
    try:
        r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "#" + color
    if factor < 0:
        scale = 1.0 + factor
        r, g, b = int(r * scale), int(g * scale), int(b * scale)
    else:
        r = int(r + (255 - r) * factor)
        g = int(g + (255 - g) * factor)
        b = int(b + (255 - b) * factor)
    clamp = lambda v: max(0, min(255, v))
    return "#{:02x}{:02x}{:02x}".format(clamp(r), clamp(g), clamp(b))


def dialog_stylesheet(p: dict[str, str]) -> str:
    """Build a skin-driven QSS for popup dialogs.

    Every selector used across the app's dialogs is defined here in terms of
    the active palette, so popups stay in step with the chosen skin.
    """
    bg = p.get("bg", "#f4f7fb")
    panel = p.get("panel", "#ffffff")
    panel_alt = p.get("panel_alt", "#f8fafc")
    line = p.get("line", "#dbe3ec")
    line_soft = p.get("line_soft", "#e5ebf2")
    text = p.get("text", "#1f2328")
    muted = p.get("muted", "#667085")
    accent = p.get("accent", "#1f7a67")
    accent_soft = p.get("accent_soft", "#e7f4f0")
    danger = p.get("danger", "#d15d48")
    button_text = p.get("button_text", "#ffffff")
    accent_hover = _shade(accent, -0.12)
    accent_grad_end = _shade(accent, 0.15)
    check_icon = asset_path("checkmark.svg").as_posix()

    return f"""
    QWidget {{
        font-family: {font_family_css()};
    }}
    QDialog, QDialog#entryDialog {{
        background: {bg};
        color: {text};
        font-family: {font_family_css()};
        font-size: 13px;
    }}
    QLabel {{
        color: {text};
    }}
    QLabel#title, QLabel#itemTitle {{
        color: {text};
        font-size: 18px;
        font-weight: 700;
    }}
    QLabel#sectionTitle {{
        color: {text};
        font-size: 13px;
        font-weight: 700;
    }}
    QLabel#subtitle, QLabel#description, QLabel#itemDesc {{
        color: {muted};
        font-size: 12px;
    }}
    QLabel#muted, QLabel#hint, QLabel#alarmInfo, QLabel#info_label {{
        color: {muted};
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel#value, QLabel#alarmTitle {{
        color: {text};
        font-size: 13px;
    }}
    QLabel#alarmTime {{
        color: {text};
        font-size: 20px;
        font-weight: 700;
    }}
    QLabel#warning_label {{
        color: {danger};
        font-size: 11px;
        font-weight: 600;
    }}
    QFrame#card, QFrame#softCard, QFrame#itemCard, QFrame#alarmItem {{
        border: 1px solid {line};
        border-radius: 12px;
    }}
    QFrame#card, QFrame#itemCard, QFrame#alarmItem {{
        background: {panel};
    }}
    QFrame#softCard {{
        background: {panel_alt};
    }}
    QFrame#card:hover {{
        border-color: {accent};
    }}
    QFrame#separator {{
        background: {line_soft};
        max-height: 1px;
        border: none;
    }}
    QFrame#headerBox {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent}, stop:1 {accent_grad_end});
        border-radius: 8px;
        padding: 12px;
    }}
    QLabel#headerTitle {{
        color: {button_text};
        font-size: 15px;
        font-weight: 700;
    }}
    QLabel#headerSubtitle {{
        color: {button_text};
        font-size: 12px;
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QTabWidget::pane {{
        border: 1px solid {line};
        border-radius: 6px;
        background: {panel};
        margin-top: -1px;
    }}
    QTabBar::tab {{
        padding: 5px 10px;
        font-size: 11px;
        font-weight: bold;
        color: {muted};
        border: 1px solid {line};
        border-bottom: none;
        background: {panel_alt};
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        color: {accent};
        background: {panel};
        border-bottom: 2px solid {accent};
    }}
    QTabBar::scroller {{
        width: 0px;
    }}
    QToolButton.sticker-btn {{
        border: 1px solid {line};
        border-radius: 6px;
        background: {panel_alt};
        font-size: 18px;
        padding: 3px;
        min-width: 38px;
        min-height: 38px;
    }}
    QToolButton.sticker-btn:hover {{
        background: {accent_soft};
        border-color: {accent};
    }}
    QLineEdit, QComboBox, QFontComboBox, QDateEdit, QTimeEdit, QSpinBox, QPlainTextEdit, QTextEdit {{
        background: {panel};
        border: 1px solid {line};
        border-radius: 8px;
        color: {text};
        padding: 4px 8px;
        selection-background-color: {accent_soft};
        selection-color: {text};
    }}
    QComboBox QLineEdit, QFontComboBox QLineEdit {{
        border: none;
        background: transparent;
        padding: 0px;
    }}
    QPlainTextEdit, QTextEdit {{
        padding: 8px;
        selection-background-color: {accent_soft};
        selection-color: {text};
    }}
    QCheckBox, QRadioButton {{
        color: {text};
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {muted};
        background: {panel};
        border-radius: 0px;
    }}
    QCheckBox::indicator:checked {{
        background: {accent_soft};
        border: 1px solid {accent};
        image: url("{check_icon}");
    }}
    QPushButton, QDialogButtonBox QPushButton {{
        background: {panel};
        border: 1px solid {line};
        border-radius: 6px;
        padding: 6px 14px;
        min-width: 80px;
        color: {text};
    }}
    QPushButton:hover, QDialogButtonBox QPushButton:hover {{
        background: {panel_alt};
    }}
    QPushButton:focus, QDialogButtonBox QPushButton:focus, QToolButton:focus {{
        outline: none;
    }}
    QPushButton#primary, QDialogButtonBox QPushButton#primary {{
        background: {accent};
        color: {button_text};
        border: 1px solid {accent};
        font-weight: 700;
    }}
    QPushButton#primary:hover, QDialogButtonBox QPushButton#primary:hover {{
        background: {accent_hover};
    }}
    QPushButton#primaryBtn {{
        background: {accent};
        color: {button_text};
        font-weight: 600;
        font-size: 12px;
        padding: 6px 18px;
        border-radius: 6px;
        border: none;
    }}
    QPushButton#primaryBtn:hover {{
        background: {accent_hover};
    }}
    QPushButton#secondary, QPushButton#secondaryBtn, QDialogButtonBox QPushButton#secondary {{
        background: {panel};
        color: {text};
        border: 1px solid {line};
        font-weight: 600;
    }}
    QPushButton#secondary:hover, QPushButton#secondaryBtn:hover, QDialogButtonBox QPushButton#secondary:hover {{
        background: {panel_alt};
    }}
    QPushButton#attachLink {{
        background: {panel};
        border: 1px solid {line};
        border-radius: 8px;
        text-align: left;
        color: {text};
        padding: 4px 10px;
    }}
    QPushButton#attachLink:hover {{
        background: {panel_alt};
    }}
    QPushButton#danger {{
        background: {panel};
        color: {danger};
        border: 1px solid {line};
    }}
    QPushButton#danger:hover {{
        background: {panel_alt};
        border: 1px solid {danger};
    }}
    QToolButton#stepButton {{
        background: {panel};
        border: 1px solid {line};
        border-radius: 6px;
        padding: 0px;
        font-size: 9px;
        color: {text};
    }}
    QToolButton#stepButton:hover {{
        background: {panel_alt};
    }}
    QToolButton#stepButton:pressed {{
        background: {accent_soft};
    }}
    QToolButton#weekdayBtn {{
        border: 1px solid {line};
        border-radius: 14px;
        background: {panel};
        color: {text};
        font-weight: 600;
    }}
    QToolButton#weekdayBtn:checked {{
        background: {accent};
        color: {button_text};
        border: 1px solid {accent};
    }}
    QListWidget#navSidebar {{
        background: {panel};
        border: 1px solid {line};
        border-radius: 10px;
        outline: none;
        padding: 6px;
        font-family: {font_family_css()};
        font-size: 14px;
        font-weight: 600;
    }}
    QListWidget#navSidebar::item {{
        height: 40px;
        min-height: 40px;
        padding-left: 10px;
        font-family: {font_family_css()};
        font-size: 14px;
        font-weight: 600;
        color: {text};
        border-radius: 6px;
    }}
    QListWidget#navSidebar::item:hover {{
        background-color: {panel_alt};
        color: {text};
    }}
    QListWidget#navSidebar::item:selected {{
        background-color: {accent_soft};
        color: {text};
        font-weight: 700;
    }}
    QComboBox:disabled, QFontComboBox:disabled, QDateEdit:disabled, QTimeEdit:disabled, QSpinBox:disabled {{
        background: {panel_alt};
        color: {muted};
        border: 1px solid {line};
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 4px 2px 4px 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {line};
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    """
