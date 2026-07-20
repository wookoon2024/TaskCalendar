from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from taskcalendar.models import CalendarEntry, EntryType
from taskcalendar.qt_dialogs import EntryDialog, SettingsDialog


def ensure_qt_application() -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    return QApplication([])


def _center_dialog_on_screen(dialog: QDialog) -> None:
    app = QApplication.instance()
    if app is None:
        return
    screen = app.primaryScreen()
    if screen is None:
        return
    available = screen.availableGeometry()
    frame = dialog.frameGeometry()
    frame.moveCenter(available.center())
    dialog.move(frame.topLeft())


def show_entry_dialog(entry_type: EntryType, selected_day: date | None, entry: CalendarEntry | None) -> CalendarEntry | None:
    app = ensure_qt_application()
    dialog = EntryDialog(None, entry_type, selected_day, entry)
    _center_dialog_on_screen(dialog)
    accepted = dialog.exec()
    _ = app
    if not accepted:
        return None
    return dialog.result


def show_settings_dialog(
    current_theme: str,
    current_shortcut: str,
    auto_start_enabled: bool,
    sticker_animation_enabled: bool = True,
    hide_completed_on_calendar: bool = True,
    auto_backup_enabled: bool = True,
    auto_backup_interval_days: int = 1,
    auto_backup_keep_count: int = 5,
    db_path: Path | None = None,
) -> dict[str, object] | None:
    from pathlib import Path
    app = ensure_qt_application()
    db_p = db_path or Path("db/taskcalendar.db.enc")
    dialog = SettingsDialog(
        None,
        current_theme,
        current_shortcut,
        auto_start_enabled,
        sticker_animation_enabled,
        hide_completed_on_calendar,
        auto_backup_enabled,
        auto_backup_interval_days,
        auto_backup_keep_count,
        db_p,
    )
    _center_dialog_on_screen(dialog)
    accepted = dialog.exec()
    _ = app
    if not accepted:
        return None
    return dialog.result
