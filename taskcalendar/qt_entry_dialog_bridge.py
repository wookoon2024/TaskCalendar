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


def show_settings_dialog(current_theme: str, current_shortcut: str, auto_start_enabled: bool) -> dict[str, object] | None:
    app = ensure_qt_application()
    dialog = SettingsDialog(None, current_theme, current_shortcut, auto_start_enabled)
    _center_dialog_on_screen(dialog)
    accepted = dialog.exec()
    _ = app
    if not accepted:
        return None
    return dialog.result
