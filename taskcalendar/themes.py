from __future__ import annotations

from tkinter import ttk


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
        "bg": "#0d0f12",
        "panel": "#14171c",
        "panel_alt": "#1a1e24",
        "line": "#282e38",
        "line_soft": "#1a1d24",
        "text": "#e2e8f0",
        "muted": "#64748b",
        "accent": "#10b981",
        "accent_soft": "#064e3b",
        "work": "#6366f1",
        "danger": "#f43f5e",
        "info": "#3b82f6",
        "button_text": "#ffffff",
        "badge_today_bg": "#312e81",
        "badge_today_fg": "#e0e7ff",
        "badge_selected_bg": "#064e3b",
        "badge_selected_fg": "#d1fae5",
        "done_panel": "#1e293b",
        "done_panel_qt": "#1e293b",
        "entry_text": "#e2e8f0",
        "more_text": "#94a3b8",
        "icon_anniversary": "#ec4899",
        "icon_important": "#f59e0b",
    },
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
