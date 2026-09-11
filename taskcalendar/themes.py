from __future__ import annotations


BODY_FONT = ("Segoe UI", 10)
SMALL_FONT = ("Segoe UI", 9)
TITLE_FONT = ("Segoe UI", 16, "bold")


THEMES = {
    "light": {
        "bg": "#e8edf3",
        "panel": "#ffffff",
        "panel_alt": "#f6f8fb",
        "line": "#d6dde6",
        "line_soft": "#e6ebf2",
        "text": "#1f2328",
        "muted": "#667085",
        "accent": "#1f7a67",
        "accent_soft": "#e7f4f0",
        "work": "#6f52d9",
        "danger": "#e15741",
        "info": "#3567d8",
        "button_text": "#ffffff",
        "badge_today_bg": "#f8dbe6",
        "badge_today_fg": "#8f3a5b",
        "badge_selected_bg": "#dff3e8",
        "badge_selected_fg": "#2f6b4f",
        "done_panel": "#dfeee1",
        "icon_anniversary": "#e55b92",
        "icon_important": "#d89b00",
    },
    "warm": {
        "bg": "#ebe4d8",
        "panel": "#fffaf2",
        "panel_alt": "#f6efe4",
        "line": "#d9cebe",
        "line_soft": "#e8dfd2",
        "text": "#312921",
        "muted": "#7c6e62",
        "accent": "#1f6f62",
        "accent_soft": "#e4f1ec",
        "work": "#8b5ad7",
        "danger": "#d15d48",
        "info": "#3f69c6",
        "button_text": "#ffffff",
        "badge_today_bg": "#f7ddd8",
        "badge_today_fg": "#8d4a3f",
        "badge_selected_bg": "#e3f0e5",
        "badge_selected_fg": "#3f6950",
        "done_panel": "#e2ecd9",
        "icon_anniversary": "#d45c88",
        "icon_important": "#b7871a",
    },
    "dark": {
        "bg": "#0a0c10",
        "panel": "#171b22",
        "panel_alt": "#20252e",
        "line": "#3a4250",
        "line_soft": "#2a303a",
        "text": "#f3f6fb",
        "muted": "#b8c5d6",
        "accent": "#10b981",
        "accent_soft": "#0b3d33",
        "work": "#a5a6fa",
        "danger": "#ff6b81",
        "info": "#60a5fa",
        "button_text": "#ffffff",
        "badge_today_bg": "#3b3a8f",
        "badge_today_fg": "#ecefff",
        "badge_selected_bg": "#0b4f3f",
        "badge_selected_fg": "#d1fae5",
        "done_panel": "#233044",
        "done_panel_qt": "#233044",
        "entry_text": "#f3f6fb",
        "more_text": "#e2e8f0",
        "icon_anniversary": "#ff7ab8",
        "icon_important": "#ffd166",
    },
    "pink": {
        "bg": "#f7e6ec",
        "panel": "#fffafc",
        "panel_alt": "#fbeef4",
        "line": "#e8c6d5",
        "line_soft": "#f3dde6",
        "text": "#3a2430",
        "muted": "#8a6b78",
        "accent": "#c2185b",
        "accent_soft": "#fce4ee",
        "work": "#8e5bd0",
        "danger": "#d1395a",
        "info": "#3f6fc9",
        "button_text": "#ffffff",
        "badge_today_bg": "#f9cfe0",
        "badge_today_fg": "#8f2f57",
        "badge_selected_bg": "#f4d3e2",
        "badge_selected_fg": "#8a2b55",
        "done_panel": "#f2e2e9",
        "done_panel_qt": "#f6e2ea",
        "entry_text": "#3a2430",
        "more_text": "#7a5a68",
        "icon_anniversary": "#e0559a",
        "icon_important": "#c58a12",
    },
    "mint": {
        "bg": "#e4f0ea",
        "panel": "#fbfffd",
        "panel_alt": "#eef8f3",
        "line": "#c4ded2",
        "line_soft": "#dcf0e7",
        "text": "#1e3a30",
        "muted": "#5f7d70",
        "accent": "#0f8f6f",
        "accent_soft": "#dcf5ec",
        "work": "#6a5bd0",
        "danger": "#d94f57",
        "info": "#2f76c9",
        "button_text": "#ffffff",
        "badge_today_bg": "#c9ecdd",
        "badge_today_fg": "#1f6b4f",
        "badge_selected_bg": "#c7efe0",
        "badge_selected_fg": "#1c6b4e",
        "done_panel": "#dff0e6",
        "done_panel_qt": "#e0f2e8",
        "entry_text": "#1e3a30",
        "more_text": "#557066",
        "icon_anniversary": "#dd5b93",
        "icon_important": "#b8860b",
    },
    "lavender": {
        "bg": "#ece8f6",
        "panel": "#fdfcff",
        "panel_alt": "#f4f1fb",
        "line": "#d6cdea",
        "line_soft": "#e8e2f6",
        "text": "#2e2640",
        "muted": "#6f6688",
        "accent": "#6d4bc4",
        "accent_soft": "#ece5fb",
        "work": "#7a5ad6",
        "danger": "#d1495b",
        "info": "#3f6fc9",
        "button_text": "#ffffff",
        "badge_today_bg": "#ded3f5",
        "badge_today_fg": "#5b3f9c",
        "badge_selected_bg": "#e0d4f7",
        "badge_selected_fg": "#523a8c",
        "done_panel": "#e6e0f2",
        "done_panel_qt": "#e8e2f5",
        "entry_text": "#2e2640",
        "more_text": "#655c80",
        "icon_anniversary": "#d15b9a",
        "icon_important": "#b8860b",
    },
}


THEME_LABELS = {
    "light": "라이트",
    "warm": "웜",
    "dark": "블랙",
    "pink": "핑크",
    "mint": "민트",
    "lavender": "라벤더",
}


def apply_theme(style: ttk.Style, theme_name: str) -> dict[str, str]:
    palette = THEMES[theme_name]
    style.theme_use("clam")
    style.configure("App.TFrame", background=palette["bg"])
    style.configure("Panel.TFrame", background=palette["panel"], relief="flat")
    style.configure("AltPanel.TFrame", background=palette["panel_alt"], relief="solid", borderwidth=1, bordercolor=palette.get("line_soft", palette["line"]))
    style.configure("SoftPanel.TFrame", background=palette["panel_alt"], relief="solid", borderwidth=1, bordercolor=palette.get("line_soft", palette["line"]))
    style.configure("DonePanel.TFrame", background=palette.get("done_panel", palette["panel_alt"]), relief="solid", borderwidth=1, bordercolor=palette.get("line_soft", palette["line"]))
    style.configure("TLabel", background=palette["panel"], foreground=palette["text"], font=BODY_FONT)
    style.configure("Muted.TLabel", background=palette["panel"], foreground=palette["muted"])
    style.configure("Soft.TLabel", background=palette["panel_alt"], foreground=palette["text"], font=BODY_FONT)
    style.configure("SoftMuted.TLabel", background=palette["panel_alt"], foreground=palette["muted"], font=BODY_FONT)
    style.configure("Title.TLabel", background=palette["panel"], foreground=palette["text"], font=TITLE_FONT)
    style.configure("Small.TLabel", background=palette["panel"], foreground=palette["muted"], font=SMALL_FONT)
    style.configure("AltMuted.TLabel", background=palette["panel_alt"], foreground=palette["muted"], font=BODY_FONT)
    style.configure("AltSmall.TLabel", background=palette["panel_alt"], foreground=palette["muted"], font=SMALL_FONT)
    style.configure(
        "TButton",
        padding=(10, 6),
        background=palette["panel"],
        foreground=palette["text"],
        bordercolor=palette["line"],
        focusthickness=0,
        font=BODY_FONT,
    )
    style.map("TButton", background=[("active", palette["panel_alt"])])
    style.configure(
        "Compact.TButton",
        padding=(6, 3),
        background=palette["panel"],
        foreground=palette["text"],
        bordercolor=palette["panel"],
        borderwidth=0,
        focusthickness=0,
        font=SMALL_FONT,
        relief="flat",
    )
    style.map(
        "Compact.TButton",
        background=[("active", palette["panel_alt"])],
        bordercolor=[("active", palette["panel_alt"])],
    )
    style.configure(
        "Topbar.TButton",
        padding=(10, 4),
        background=palette["panel"],
        foreground=palette["text"],
        bordercolor=palette["line"],
        focusthickness=0,
        font=SMALL_FONT,
    )
    style.map("Topbar.TButton", background=[("active", palette["panel_alt"])])
    style.configure("Primary.TButton", background=palette["accent"], foreground=palette["button_text"], bordercolor=palette["accent"], font=BODY_FONT)
    style.map("Primary.TButton", background=[("active", palette["accent"])])
    style.configure("TEntry", fieldbackground=palette["panel"], foreground=palette["text"], bordercolor=palette.get("line_soft", palette["line"]), padding=4, font=BODY_FONT)
    style.configure(
        "TCombobox",
        fieldbackground=palette["panel"],
        foreground=palette["text"],
        bordercolor=palette.get("line_soft", palette["line"]),
        arrowsize=14,
        padding=3,
        font=BODY_FONT,
    )
    style.configure("TCheckbutton", background=palette["panel"], foreground=palette["text"], font=BODY_FONT)
    style.configure("Alt.TCheckbutton", background=palette["panel_alt"], foreground=palette["text"], font=BODY_FONT)
    style.map("Alt.TCheckbutton", background=[("active", palette["panel_alt"])])
    style.configure("TRadiobutton", background=palette["panel"], foreground=palette["text"], font=BODY_FONT)
    style.map("TRadiobutton", background=[("active", palette["panel"])])
    style.configure("Alt.TRadiobutton", background=palette["panel_alt"], foreground=palette["text"], font=BODY_FONT)
    style.map("Alt.TRadiobutton", background=[("active", palette["panel_alt"])])

    style.layout(
        "Slim.Vertical.TScrollbar",
        [
            (
                "Vertical.Scrollbar.trough",
                {
                    "sticky": "ns",
                    "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                },
            )
        ],
    )
    style.configure(
        "Slim.Vertical.TScrollbar",
        troughcolor=palette["panel_alt"],
        background=palette["line"],
        bordercolor=palette["panel_alt"],
        darkcolor=palette["line"],
        lightcolor=palette["line"],
        arrowcolor=palette["line"],
        relief="flat",
        borderwidth=0,
        gripcount=0,
    )
    style.map("Slim.Vertical.TScrollbar", background=[("active", palette["muted"])])
    return palette
