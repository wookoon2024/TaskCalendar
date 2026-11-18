"""캘린더·문서·업무 세 창이 함께 쓰는 화면 부품.

- 버튼 크기 규격(S/M/L)과 종류(primary/secondary/ghost) 스타일
- 세 창 맨 위의 공통 전환 줄 [캘린더 | 문서 | 업무] ······ [창별 버튼] [기능] [설정]
- 업무 창 방식의 사이드바 접기 부품 (경계선 가운데 작은 화살표 + 접힌 띠)
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from taskcalendar.fonts import font_family_css

# 높이(px), 글자 크기(px)
BUTTON_SIZES: dict[str, tuple[int, int]] = {"S": (24, 11), "M": (30, 12), "L": (34, 13)}
BUTTON_RADIUS = 6

WINDOW_TITLES = {"calendar": "캘린더", "memo": "메모", "doc": "문서", "work": "업무"}
BAND_HEIGHT = 40  # 세 창 맨 위 띠의 높이


def _shade(hex_color: str, factor: float) -> str:
    color = hex_color.lstrip("#")
    if len(color) != 6:
        return hex_color
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    if factor < 0:
        r, g, b = (int(c * (1 + factor)) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * factor) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def button_qss(palette: dict, kind: str = "secondary", size: str = "M") -> str:
    """S/M/L 규격 버튼 스타일. kind: primary(채움, 창마다 1개) / secondary(테두리, 기본) / ghost(배경 없음)."""
    height, font_px = BUTTON_SIZES.get(size, BUTTON_SIZES["M"])
    panel = palette.get("panel", "#FFFFFF")
    panel_alt = palette.get("panel_alt", "#F8FAFC")
    text = palette.get("text", "#1F2328")
    line = palette.get("line", "#CBD5E0")
    accent = palette.get("accent", "#1F7A67")
    on_accent = palette.get("button_text", "#FFFFFF")
    pad = 8 if size == "S" else 12
    if kind == "primary":
        normal = f"background-color: {accent}; color: {on_accent}; border: 1px solid {accent}; font-weight: 600;"
        hover = f"background-color: {_shade(accent, -0.12)}; border-color: {_shade(accent, -0.12)};"
    elif kind == "ghost":
        normal = f"background-color: transparent; color: {text}; border: 1px solid transparent;"
        hover = f"background-color: {panel_alt};"
    else:
        normal = f"background-color: {panel}; color: {text}; border: 1px solid {line};"
        hover = f"background-color: {panel_alt}; border-color: {accent};"
    return f"""
        QPushButton {{
            {normal}
            border-radius: {BUTTON_RADIUS}px;
            min-height: {height - 2}px;
            max-height: {height}px;
            padding: 0 {pad}px;
            font-size: {font_px}px;
            font-family: {font_family_css()};
        }}
        QPushButton:hover {{ {hover} }}
        QPushButton:disabled {{ color: {palette.get('muted', '#94A3B8')}; }}
    """


def style_button(button: QPushButton, palette: dict, kind: str = "secondary", size: str = "M") -> QPushButton:
    button.setStyleSheet(button_qss(palette, kind, size))
    button.setFixedHeight(BUTTON_SIZES.get(size, BUTTON_SIZES["M"])[0])
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class WindowSwitcherBar(QFrame):
    """세 창 맨 윗줄. 왼쪽은 창 전환 버튼, 오른쪽은 창별 버튼(add_action)과 공통 [기능] [설정].

    switch_to(name) 은 'calendar' / 'doc' / 'work' 중 하나를 받아 해당 창을 앞으로 가져온다.
    show_service_menu(button) 과 open_settings() 는 메인(캘린더) 창의 기능 메뉴·환경설정을 연다.
    """

    def __init__(
        self,
        palette: dict,
        active: str,
        switch_to: Callable[[str], None],
        show_service_menu: Callable[[QPushButton], None] | None = None,
        open_settings: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("windowSwitcherBar")
        self._palette = palette
        self._active = active
        self._switch_to = switch_to

        self.setFixedHeight(BAND_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 10, 0)
        layout.setSpacing(6)
        self._tabs_layout = QHBoxLayout()
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(0)
        layout.addLayout(self._tabs_layout)

        self.tab_buttons: dict[str, QPushButton] = {}
        for name, label in WINDOW_TITLES.items():
            btn = QPushButton(label)
            btn.setToolTip(f"{label} 보기")
            btn.clicked.connect(lambda _=False, n=name: self._on_tab_clicked(n))
            self._tabs_layout.addWidget(btn)
            self.tab_buttons[name] = btn

        layout.addStretch(1)
        self._actions_layout = QHBoxLayout()
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(6)
        layout.addLayout(self._actions_layout)

        self.service_button = QPushButton("부가 기능 ▾")  # 업무 화면의 [부가 기능 ▾] 와 같은 이름·모양
        self.service_button.setToolTip("부가기능 (달력 인쇄·꾸미기, 알람 설정, 날짜 계산기, 바탕화면 위젯)")
        self.settings_button = QPushButton("설정")
        self.settings_button.setToolTip("환경설정")
        if show_service_menu:
            self.service_button.clicked.connect(lambda: show_service_menu(self.service_button))
            layout.addWidget(self.service_button, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.service_button.hide()
        if open_settings:
            self.settings_button.clicked.connect(open_settings)
            layout.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.settings_button.hide()
        self._trailing_layout = QHBoxLayout()
        self._trailing_layout.setContentsMargins(4, 0, 0, 0)
        self._trailing_layout.setSpacing(4)
        layout.addLayout(self._trailing_layout)

        self._actions: list[tuple[QPushButton, str]] = []
        self._action_pages: dict[QPushButton, str | None] = {}
        self.apply_palette(palette)

    def add_action(self, button: QPushButton, kind: str = "secondary", page: str | None = None) -> QPushButton:
        """창별 버튼을 [기능] [설정] 앞에 붙인다. page 를 주면 그 탭이 선택됐을 때만 보인다."""
        self._actions.append((button, kind))
        self._action_pages[button] = page
        self._actions_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        style_button(button, self._palette, kind, "M")
        button.setVisible(page is None or page == self._active)
        return button

    def set_active(self, name: str) -> None:
        """선택된 탭을 바꾸고, 그 탭 전용 버튼만 보이게 한다."""
        self._active = name
        for button, page in self._action_pages.items():
            button.setVisible(page is None or page == name)
        self.apply_palette(self._palette)

    def add_trailing(self, widget: QWidget) -> QWidget:
        """[설정] 뒤 맨 오른쪽에 작은 아이콘 버튼 등을 붙인다 (스타일은 그대로 둠)."""
        self._trailing_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def _on_tab_clicked(self, name: str) -> None:
        self._switch_to(name)

    def apply_palette(self, palette: dict) -> None:
        self._palette = palette
        accent = palette.get("accent", "#1F7A67")
        text = palette.get("text", "#1F2328")
        muted = palette.get("muted", "#64748B")
        line = palette.get("line_soft", palette.get("line", "#E2E8F0"))
        self.setStyleSheet(
            f"QFrame#windowSwitcherBar {{ background-color: {palette.get('panel', '#FFFFFF')};"
            f" border: none; border-bottom: 1px solid {line}; }}"
        )
        for name, btn in self.tab_buttons.items():
            active = name == self._active
            btn.setFixedHeight(BAND_HEIGHT)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {accent if active else muted};
                    border: none;
                    border-bottom: 2px solid {accent if active else 'transparent'};
                    border-radius: 0px;
                    padding: 0 14px;
                    font-size: 13px;
                    font-weight: {600 if active else 500};
                    font-family: {font_family_css()};
                }}
                QPushButton:hover {{ color: {accent if active else text}; }}
            """)
        style_button(self.service_button, palette, "secondary", "M")
        style_button(self.settings_button, palette, "secondary", "M")
        for button, kind in self._actions:
            style_button(button, palette, kind, "M")


UNTITLED_MEMO_TITLES = {"", "제목 없음", "(제목 없음)"}


def memo_display_title(title: str, plain_body: str, limit: int = 40) -> tuple[str, bool]:
    """메모 카드에 보일 제목. 제목이 비어 있으면 본문 첫 줄을 제목으로 쓴다.

    돌려주는 값: (보일 제목, 본문 첫 줄을 제목으로 썼는지)
    """
    clean = (title or "").strip()
    if clean not in UNTITLED_MEMO_TITLES:
        return clean, False
    for line in (plain_body or "").splitlines():
        line = line.strip()
        if line:
            return (line[:limit].rstrip() + "…") if len(line) > limit else line, True
    return "새 메모", False


def menu_qss(palette: dict) -> str:
    """앱 전체에서 같은 모양으로 쓰는 펼침 메뉴(QMenu) 스타일. 버튼(M)과 같은 12px 글씨.

    배경과 섞이지 않도록 메뉴는 화면보다 한 단계 밝은 바탕(밝은 테마는 흰색)과 진한 테두리를 쓴다.
    """
    from PySide6.QtGui import QColor

    panel = QColor(palette.get("panel", "#FFFFFF"))
    is_dark = panel.lightness() < 128
    bg = panel.lighter(135).name() if is_dark else "#FFFFFF"
    line = QColor(palette.get("line", "#CBD5E0"))
    border = line.lighter(150).name() if is_dark else line.darker(135).name()
    text = palette.get("text", "#1F2328")
    muted = palette.get("muted", "#64748B")
    line_soft = palette.get("line_soft", palette.get("line", "#CBD5E0"))
    accent = palette.get("accent", "#1F7A67")
    accent_soft = palette.get("accent_soft", palette.get("panel_alt", "#EEF2F7"))
    return f"""
        QMenu {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: 8px;
            padding: 5px;
            font-family: {font_family_css()};
            font-size: 12px;
        }}
        QMenu::item {{
            color: {text};
            padding: 7px 24px 7px 12px;
            border-radius: 6px;
            min-width: 120px;
        }}
        QMenu::item:selected {{
            background-color: {accent_soft};
            color: {accent};
            font-weight: 600;
        }}
        QMenu::item:disabled {{ color: {muted}; }}
        QMenu::separator {{
            height: 1px;
            background: {line_soft};
            margin: 4px 6px;
        }}
    """


_MENU_BASE_STYLE = None


def _menu_base_style():
    """메뉴는 Fusion 스타일 위에 그린다. 윈도우11 기본 스타일은 메뉴에 자체 그림자·모서리를 덧그려
    둥근 테두리 오른쪽 아래에 검은 자국이 남는다."""
    global _MENU_BASE_STYLE
    if _MENU_BASE_STYLE is None:
        from PySide6.QtWidgets import QApplication, QStyleFactory

        _MENU_BASE_STYLE = QStyleFactory.create("Fusion")
        if _MENU_BASE_STYLE is not None and QApplication.instance() is not None:
            _MENU_BASE_STYLE.setParent(QApplication.instance())
    return _MENU_BASE_STYLE


def style_menu(menu, palette: dict):
    """메뉴에 공통 스타일을 입힌다 (테두리 둥근 흰 바탕, 12px, 테마 색 강조, 아이콘 없음).

    - 둥근 모서리 바깥이 각지게 비치지 않도록 창 바탕을 투명하게 둔다.
    - 윈도우가 그려 주는 네모 그림자는 둥근 모서리 밖으로 검게 비치므로 끈다.
    """
    key = f"{palette.get('panel')}|{palette.get('accent')}|{palette.get('text')}"
    if menu.property("tcMenuStyle") == key:
        return menu
    menu.setProperty("tcMenuStyle", key)
    if not menu.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        menu.setWindowFlags(
            menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    base = _menu_base_style()
    if base is not None and menu.style() is not base:
        menu.setStyle(base)
    menu.setStyleSheet(menu_qss(palette))
    return menu


def install_menu_styler(app, palette_getter) -> None:
    """앱의 모든 펼침 메뉴(우클릭 메뉴·하위 메뉴·입력칸 기본 메뉴 포함)에 같은 디자인을 입힌다.

    메뉴가 처음 화면에 뜨기 직전(Polish)에 현재 테마로 스타일을 덮어쓴다.
    """
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QMenu

    class _MenuStyler(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() in (QEvent.Type.Polish, QEvent.Type.Show) and isinstance(obj, QMenu):
                try:
                    style_menu(obj, palette_getter())
                except Exception:
                    pass
            return False

    old = getattr(app, "_tc_menu_styler", None)
    if old is not None:
        app.removeEventFilter(old)
    styler = _MenuStyler(app)
    app._tc_menu_styler = styler
    app.installEventFilter(styler)


# ── 사이드바 접기 (업무 창과 같은 방식) ─────────────────────────────────

def gutter_arrow_qss(palette: dict) -> str:
    line = palette.get("line", "#CBD5E0")
    panel_alt = palette.get("panel_alt", "#F8FAFC")
    muted = palette.get("text_muted", palette.get("muted", "#64748B"))
    accent = palette.get("accent", "#2563EB")
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {muted};
            border: 1px solid transparent;
            border-radius: 2px;
            font-weight: bold;
            font-size: 8px;
            padding: 0;
        }}
        QPushButton:hover {{
            background-color: {panel_alt};
            color: {accent};
            border: 1px solid {line};
        }}
    """


def make_gutter(palette: dict, arrow: str, tooltip: str, on_click: Callable[[], None]) -> QWidget:
    """패널 경계선 세로 가운데에 놓이는 작은 접기 버튼 기둥 (업무 창 [◀] 과 같은 모양)."""
    gutter = QFrame()
    gutter.setObjectName("collapseGutter")
    gutter.setFixedWidth(11)
    gutter.setStyleSheet(f"QFrame#collapseGutter {{ background-color: {palette.get('panel', '#FFFFFF')}; border: none; }}")
    layout = QVBoxLayout(gutter)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addStretch(1)
    btn = QPushButton(arrow)
    btn.setToolTip(tooltip)
    btn.setFixedSize(11, 36)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(gutter_arrow_qss(palette))
    btn.clicked.connect(on_click)
    layout.addWidget(btn)
    layout.addStretch(1)
    gutter.arrow_button = btn  # type: ignore[attr-defined]
    return gutter


def make_collapsed_strip(palette: dict, arrow: str, tooltip: str, on_click: Callable[[], None]) -> QFrame:
    """패널을 접었을 때 남는 얇은 띠. 띠 아무 곳이나 누르면 다시 펼친다."""
    strip = QFrame()
    strip.setFixedWidth(14)
    strip.setCursor(Qt.CursorShape.PointingHandCursor)
    strip.setToolTip(tooltip)
    strip.setStyleSheet(
        f"QFrame {{ background-color: {palette.get('panel', '#FFFFFF')};"
        f" border: none; border-left: 1px solid {palette.get('line_soft', palette.get('line', '#E2E8F0'))}; }}"
    )
    layout = QVBoxLayout(strip)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addStretch(1)
    btn = QPushButton(arrow)
    btn.setToolTip(tooltip)
    btn.setFixedSize(12, 36)
    btn.setStyleSheet(gutter_arrow_qss(palette) + "QPushButton { border: none; }")
    btn.clicked.connect(on_click)
    layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
    layout.addStretch(1)
    strip.mousePressEvent = lambda _e: on_click()  # type: ignore[method-assign]
    return strip


def embed_window_page(dialog: QWidget, band: "WindowSwitcherBar", page: str) -> QWidget:
    """따로 띄우던 문서·업무 창(QDialog)을 한 창 안의 탭 페이지로 넣는다.

    - 창 테두리를 없애고 일반 위젯으로 바꾼다.
    - 자기 맨 위 띠는 숨기고, 띠에 있던 버튼은 한 창의 띠로 옮겨 그 탭에서만 보이게 한다.
    - Esc 로 페이지가 숨겨지지 않게 reject 를 막는다 (Esc 의 다른 동작은 그대로).
    """
    import types

    dialog.setWindowFlags(Qt.WindowType.Widget)
    try:
        dialog.setSizeGripEnabled(False)
    except Exception:
        pass
    own = getattr(dialog, "window_switcher", None)
    if own is not None and own is not band:
        own.hide()
        for button, kind in list(getattr(own, "_actions", [])):
            band.add_action(button, kind, page)
    # 전체화면 전환·팔레트 갱신이 한 창의 띠에 적용되도록 가리킨다.
    dialog.window_switcher = band
    dialog.reject = types.MethodType(lambda self: None, dialog)
    dialog._embedded_page = True
    return dialog

