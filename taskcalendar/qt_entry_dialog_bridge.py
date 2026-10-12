from __future__ import annotations

from datetime import date

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from taskcalendar.models import CalendarEntry, EntryType
from taskcalendar.qt_dialogs import EntryDialog, SettingsDialog


class _DialogLoopQuitFilter(QObject):
    """대화상자가 닫히거나 숨겨질 때 로컬 이벤트 루프를 안전하게 종료하는 필터"""

    def __init__(self, dlg: QDialog, loop: QEventLoop) -> None:
        super().__init__(dlg)
        self._dlg = dlg
        self._loop = loop

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self._dlg and ev.type() in (QEvent.Type.Close, QEvent.Type.Hide):
            QTimer.singleShot(0, self._loop.quit)
        return False


def _non_blocking_memo_dialog_exec(self: QDialog) -> int:
    """
    QDialog.exec() 호출 시 ApplicationModal로 인해 바탕화면 스티커 메모 등
    다른 독립 창까지 클릭이 막히는 현상을 방지하기 위해
    NonModal + 로컬 QEventLoop 방식으로 동기 실행합니다.
    """
    try:
        QDialog.setResult(self, int(QDialog.DialogCode.Rejected))
    except Exception:
        pass
    self.setModal(False)
    self.setWindowModality(Qt.WindowModality.NonModal)

    loop = QEventLoop(self)
    flt = _DialogLoopQuitFilter(self, loop)
    self.installEventFilter(flt)

    dlg_result = [int(QDialog.DialogCode.Rejected)]

    def _on_finished(code: int = 0) -> None:
        dlg_result[0] = int(code)
        loop.quit()

    self.finished.connect(_on_finished)
    self.destroyed.connect(loop.quit)

    self.show()
    self.raise_()
    self.activateWindow()
    loop.exec()
    try:
        self.removeEventFilter(flt)
    except Exception:
        pass
    try:
        return int(QDialog.result(self))
    except Exception:
        return int(dlg_result[0])


def _install_non_blocking_dialog_exec() -> None:
    if not getattr(QDialog, "_memo_non_blocking_patched", False):
        QDialog._orig_qt_exec = QDialog.exec
        QDialog.exec = _non_blocking_memo_dialog_exec
        QDialog.exec_ = _non_blocking_memo_dialog_exec
        QDialog._memo_non_blocking_patched = True


_install_non_blocking_dialog_exec()


def ensure_qt_application() -> QApplication:
    _install_non_blocking_dialog_exec()
    app = QApplication.instance()
    if app is not None:
        app.setQuitOnLastWindowClosed(False)
        return app
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


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
    show_lunar_calendar: bool = True,
    show_solar_terms: bool = True,
    lunar_display_frequency: str = "all",
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
        show_lunar_calendar=show_lunar_calendar,
        show_solar_terms=show_solar_terms,
        lunar_display_frequency=lunar_display_frequency,
    )
    _center_dialog_on_screen(dialog)
    accepted = dialog.exec()
    _ = app
    if not accepted:
        return None
    return dialog.result
