from __future__ import annotations

import calendar
import ctypes
import json
import shutil
import sqlite3
from ctypes import wintypes
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from taskcalendar.models import AlertType, CalendarEntry, DaySummary, EntryType, RecurrenceType


CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


crypt32 = ctypes.windll.crypt32
kernel32 = ctypes.windll.kernel32


def _bytes_to_blob(data: bytes) -> DATA_BLOB:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))


def _blob_to_bytes(blob: DATA_BLOB) -> bytes:
    pointer = ctypes.cast(blob.pbData, ctypes.POINTER(ctypes.c_char))
    return pointer[: blob.cbData]


def protect_bytes(data: bytes) -> bytes:
    in_blob = _bytes_to_blob(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "TaskCalendar".encode("utf-16-le"),
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return _blob_to_bytes(out_blob)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def unprotect_bytes(data: bytes) -> bytes:
    in_blob = _bytes_to_blob(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return _blob_to_bytes(out_blob)
    finally:
        kernel32.LocalFree(out_blob.pbData)


class EncryptedRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachments_root = self.db_path.parent / "attachments"
        self.attachments_root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self._initialize()
        if self.db_path.exists():
            self._load()
        self._ensure_columns()
        if not self.db_path.exists():
            self._seed()
            self.save()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                day TEXT,
                start_date TEXT,
                end_date TEXT,
                start_time TEXT NOT NULL DEFAULT '',
                end_time TEXT NOT NULL DEFAULT '',
                all_day INTEGER NOT NULL DEFAULT 0,
                assignee TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                attachments_json TEXT NOT NULL DEFAULT '[]',
                recurrence_enabled INTEGER NOT NULL DEFAULT 0,
                recurrence_type TEXT NOT NULL DEFAULT 'none',
                recurrence_interval INTEGER NOT NULL DEFAULT 1,
                recurrence_weekdays_json TEXT NOT NULL DEFAULT '[]',
                recurrence_month_day INTEGER NOT NULL DEFAULT 1,
                recurrence_month_week INTEGER NOT NULL DEFAULT 1,
                recurrence_month_end INTEGER NOT NULL DEFAULT 0,
                completed_dates_json TEXT NOT NULL DEFAULT '[]',
                icon_type TEXT NOT NULL DEFAULT '',
                alert_type TEXT NOT NULL DEFAULT 'none',
                alert_offset TEXT NOT NULL DEFAULT 'at_start',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def _ensure_columns(self) -> None:
        existing = {row["name"] for row in self.connection.execute("PRAGMA table_info(entries)").fetchall()}
        additions = {
            "all_day": "ALTER TABLE entries ADD COLUMN all_day INTEGER NOT NULL DEFAULT 0",
            "recurrence_enabled": "ALTER TABLE entries ADD COLUMN recurrence_enabled INTEGER NOT NULL DEFAULT 0",
            "recurrence_type": "ALTER TABLE entries ADD COLUMN recurrence_type TEXT NOT NULL DEFAULT 'none'",
            "recurrence_interval": "ALTER TABLE entries ADD COLUMN recurrence_interval INTEGER NOT NULL DEFAULT 1",
            "recurrence_weekdays_json": "ALTER TABLE entries ADD COLUMN recurrence_weekdays_json TEXT NOT NULL DEFAULT '[]'",
            "recurrence_month_day": "ALTER TABLE entries ADD COLUMN recurrence_month_day INTEGER NOT NULL DEFAULT 1",
            "recurrence_month_week": "ALTER TABLE entries ADD COLUMN recurrence_month_week INTEGER NOT NULL DEFAULT 1",
            "recurrence_month_end": "ALTER TABLE entries ADD COLUMN recurrence_month_end INTEGER NOT NULL DEFAULT 0",
            "completed_dates_json": "ALTER TABLE entries ADD COLUMN completed_dates_json TEXT NOT NULL DEFAULT '[]'",
            "icon_type": "ALTER TABLE entries ADD COLUMN icon_type TEXT NOT NULL DEFAULT ''",
            "alert_type": "ALTER TABLE entries ADD COLUMN alert_type TEXT NOT NULL DEFAULT 'none'",
            "alert_offset": "ALTER TABLE entries ADD COLUMN alert_offset TEXT NOT NULL DEFAULT 'at_start'",
        }
        for name, sql in additions.items():
            if name not in existing:
                self.connection.execute(sql)
        self.connection.commit()

    def _load(self) -> None:
        candidates = [self.db_path, self.db_path.with_suffix(self.db_path.suffix + ".bak")]
        last_error: OSError | None = None

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                plain = unprotect_bytes(candidate.read_bytes())
            except OSError as exc:
                last_error = exc
                continue
            self.connection.deserialize(plain)
            self.connection.row_factory = sqlite3.Row
            return

        if last_error is not None:
            raise last_error

    def save(self) -> None:
        self.connection.commit()
        self.db_path.write_bytes(protect_bytes(self.connection.serialize()))

    def _seed(self) -> None:
        # Keep initial DB empty; only default settings are seeded.
        self.set_setting("theme", "light")

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connection.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def upsert_entry(self, entry: CalendarEntry) -> CalendarEntry:
        now = datetime.now().isoformat(timespec="seconds")
        if entry.entry_type != EntryType.MEMO and entry.day is None:
            entry.day = entry.start_date or date.today()
        previous_attachments: list[str] = []
        if entry.entry_id is not None:
            previous_row = self.connection.execute("SELECT attachments_json FROM entries WHERE id = ?", (entry.entry_id,)).fetchone()
            if previous_row:
                previous_attachments = json.loads(previous_row["attachments_json"] or "[]")
        entry.attachments = self._materialize_attachments(entry, previous_attachments)
        values = (
            entry.entry_type.value,
            entry.title,
            entry.description,
            entry.day.isoformat() if entry.day else None,
            entry.start_date.isoformat() if entry.start_date else None,
            entry.end_date.isoformat() if entry.end_date else None,
            entry.start_time,
            entry.end_time,
            int(entry.all_day),
            entry.assignee,
            entry.status,
            json.dumps(entry.attachments, ensure_ascii=False),
            int(entry.recurrence_enabled),
            entry.recurrence_type.value,
            max(1, int(entry.recurrence_interval or 1)),
            json.dumps(entry.recurrence_weekdays, ensure_ascii=False),
            max(1, int(entry.recurrence_month_day or 1)),
            int(entry.recurrence_month_week or 1),
            int(entry.recurrence_month_end),
            json.dumps(entry.completed_dates, ensure_ascii=False),
            entry.icon_type,
            entry.alert_type.value,
            entry.alert_offset,
        )
        if entry.entry_id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO entries (
                    entry_type, title, description, day, start_date, end_date,
                    start_time, end_time, all_day, assignee, status, attachments_json,
                    recurrence_enabled, recurrence_type, recurrence_interval,
                    recurrence_weekdays_json, recurrence_month_day, recurrence_month_week, recurrence_month_end, completed_dates_json, icon_type, alert_type, alert_offset,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values + (now, now),
            )
            entry.entry_id = int(cursor.lastrowid)
        else:
            self.connection.execute(
                """
                UPDATE entries
                SET entry_type=?, title=?, description=?, day=?, start_date=?, end_date=?,
                    start_time=?, end_time=?, all_day=?, assignee=?, status=?, attachments_json=?,
                    recurrence_enabled=?, recurrence_type=?, recurrence_interval=?,
                    recurrence_weekdays_json=?, recurrence_month_day=?, recurrence_month_week=?, recurrence_month_end=?, completed_dates_json=?, icon_type=?, alert_type=?, alert_offset=?, updated_at=?
                WHERE id = ?
                """,
                values + (now, entry.entry_id),
            )
        self._cleanup_unused_attachments(previous_attachments, entry.entry_id)
        self.connection.commit()
        return entry

    def delete_entry(self, entry_id: int) -> None:
        row = self.connection.execute("SELECT attachments_json FROM entries WHERE id = ?", (entry_id,)).fetchone()
        attachments = json.loads(row["attachments_json"] or "[]") if row else []
        self.connection.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self._cleanup_unused_attachments(attachments, entry_id)
        self.connection.commit()

    def list_entries_for_month(self, year: int, month: int) -> list[CalendarEntry]:
        rows = self.connection.execute("SELECT * FROM entries WHERE entry_type IN (?, ?)", (EntryType.SCHEDULE.value, EntryType.TASK.value)).fetchall()
        items: list[CalendarEntry] = []
        for row in rows:
            items.extend(self._expand_entry_for_month(self._row_to_entry(row), year, month))
        items.sort(key=lambda item: ((item.day or date.min), item.start_time, item.entry_id or 0))
        return items

    def list_entries_for_day(self, target_day: date) -> list[CalendarEntry]:
        rows = self.connection.execute("SELECT * FROM entries WHERE entry_type IN (?, ?)", (EntryType.SCHEDULE.value, EntryType.TASK.value)).fetchall()
        items: list[CalendarEntry] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if self._occurs_on(entry, target_day):
                items.append(replace(entry, day=target_day, source_entry_id=entry.entry_id))
        items.sort(key=lambda item: (0 if item.entry_type == EntryType.TASK else 1, item.start_time, item.entry_id or 0))
        return items

    def list_memos(self) -> list[CalendarEntry]:
        rows = self.connection.execute("SELECT * FROM entries WHERE entry_type = ? ORDER BY updated_at DESC, id DESC", (EntryType.MEMO.value,)).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get_entry(self, entry_id: int) -> CalendarEntry | None:
        row = self.connection.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_entry(row) if row else None

    def search_entries(self, keyword: str, limit: int = 200) -> list[CalendarEntry]:
        term = (keyword or "").strip()
        if not term:
            return []
        like = f"%{term}%"
        rows = self.connection.execute(
            """
            SELECT *
            FROM entries
            WHERE title LIKE ? COLLATE NOCASE OR description LIKE ? COLLATE NOCASE
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (like, like, max(1, int(limit))),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def list_all_entries(self) -> list[CalendarEntry]:
        rows = self.connection.execute("SELECT * FROM entries ORDER BY created_at ASC, id ASC").fetchall()
        return [self._row_to_entry(row) for row in rows]

    def replace_all_entries(self, entries: list[CalendarEntry]) -> int:
        rows = self.connection.execute("SELECT attachments_json FROM entries").fetchall()
        old_attachments: list[str] = []
        for row in rows:
            old_attachments.extend(json.loads(row["attachments_json"] or "[]"))

        self.connection.execute("DELETE FROM entries")
        self.connection.commit()
        for entry in entries:
            cloned = replace(entry, entry_id=None, source_entry_id=None, created_at=None, updated_at=None)
            self.upsert_entry(cloned)
        self.connection.commit()
        self._cleanup_unused_attachments(old_attachments, None)
        self.connection.commit()
        return len(entries)

    def day_summary_for_today(self) -> DaySummary:
        today = date.today()
        today_entries = [entry for entry in self.list_entries_for_day(today) if not self._is_completed_on_day(entry, today)]
        return DaySummary(
            schedules=sum(1 for entry in today_entries if entry.entry_type == EntryType.SCHEDULE),
            tasks=sum(1 for entry in today_entries if entry.entry_type == EntryType.TASK),
        )

    def resolve_attachment_path(self, stored_path: str) -> Path:
        path = Path(stored_path)
        if path.is_absolute():
            return path
        return self.attachments_root / path

    def _expand_entry_for_month(self, entry: CalendarEntry, year: int, month: int) -> list[CalendarEntry]:
        items: list[CalendarEntry] = []
        for target_day in calendar_days(year, month):
            if self._occurs_on(entry, target_day):
                items.append(replace(entry, day=target_day, source_entry_id=entry.entry_id))
        return items

    def _occurs_on(self, entry: CalendarEntry, target_day: date) -> bool:
        if entry.entry_type == EntryType.MEMO:
            return False
        anchor = entry.start_date or entry.day
        if anchor is None or target_day < anchor:
            return False
        if entry.end_date and target_day > entry.end_date:
            return False
        if not entry.recurrence_enabled or entry.recurrence_type == RecurrenceType.NONE:
            span_end = entry.end_date or entry.day or anchor
            return anchor <= target_day <= span_end

        interval = max(1, entry.recurrence_interval)
        if entry.recurrence_type == RecurrenceType.DAILY:
            return (target_day - anchor).days % interval == 0
        if entry.recurrence_type == RecurrenceType.WEEKLY:
            weekday = (target_day.weekday() + 1) % 7
            weekdays = entry.recurrence_weekdays or [(anchor.weekday() + 1) % 7]
            week_delta = (target_day - anchor).days // 7
            return weekday in weekdays and week_delta % interval == 0
        if entry.recurrence_type == RecurrenceType.MONTHLY:
            month_delta = (target_day.year - anchor.year) * 12 + (target_day.month - anchor.month)
            if month_delta < 0 or month_delta % interval != 0:
                return False
            if entry.recurrence_month_end:
                last_day = calendar.monthrange(target_day.year, target_day.month)[1]
                return target_day.day == last_day
            return target_day.day == entry.recurrence_month_day
        if entry.recurrence_type == RecurrenceType.MONTHLY_NTH:
            month_delta = (target_day.year - anchor.year) * 12 + (target_day.month - anchor.month)
            if month_delta < 0 or month_delta % interval != 0:
                return False
            weekday = entry.recurrence_weekdays[0] if entry.recurrence_weekdays else ((anchor.weekday() + 1) % 7)
            target_weekday = (target_day.weekday() + 1) % 7
            if target_weekday != weekday:
                return False
            week_no = int(entry.recurrence_month_week or 1)
            if week_no == -1:
                return (target_day + timedelta(days=7)).month != target_day.month
            occurrence = ((target_day.day - 1) // 7) + 1
            return occurrence == week_no
        if entry.recurrence_type == RecurrenceType.YEARLY:
            return target_day.month == anchor.month and target_day.day == anchor.day and target_day.year >= anchor.year
        return False

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CalendarEntry:
        return CalendarEntry(
            entry_id=row["id"],
            entry_type=EntryType(row["entry_type"]),
            title=row["title"],
            description=row["description"],
            day=date.fromisoformat(row["day"]) if row["day"] else None,
            start_date=date.fromisoformat(row["start_date"]) if row["start_date"] else None,
            end_date=date.fromisoformat(row["end_date"]) if row["end_date"] else None,
            start_time=row["start_time"] or "",
            end_time=row["end_time"] or "",
            all_day=bool(row["all_day"]),
            assignee=row["assignee"] or "",
            status=row["status"] or "",
            attachments=json.loads(row["attachments_json"] or "[]"),
            recurrence_enabled=bool(row["recurrence_enabled"]),
            recurrence_type=RecurrenceType(row["recurrence_type"] or "none"),
            recurrence_interval=int(row["recurrence_interval"] or 1),
            recurrence_weekdays=json.loads(row["recurrence_weekdays_json"] or "[]"),
            recurrence_month_day=int(row["recurrence_month_day"] or 1),
            recurrence_month_week=int(row["recurrence_month_week"] or 1),
            recurrence_month_end=bool(row["recurrence_month_end"]) if "recurrence_month_end" in row.keys() else False,
            completed_dates=json.loads(row["completed_dates_json"] or "[]"),
            icon_type=row["icon_type"] or "",
            alert_type=AlertType(row["alert_type"] or "none"),
            alert_offset=row["alert_offset"] or "at_start",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _is_completed_on_day(entry: CalendarEntry, target_day: date) -> bool:
        if entry.recurrence_enabled:
            return target_day.isoformat() in entry.completed_dates
        return entry.status == "?꾨즺"

    def _materialize_attachments(self, entry: CalendarEntry, previous_attachments: list[str]) -> list[str]:
        resolved: list[str] = []
        anchor = entry.start_date or entry.day or date.today()

        for raw_path in entry.attachments:
            path = Path(raw_path)
            if self._is_managed_attachment(path):
                rel = str(path.as_posix())
                if rel not in resolved:
                    resolved.append(rel)
                continue

            if path.is_absolute() and path.exists() and path.is_file():
                copied = self._copy_attachment_to_store(path, anchor)
                if copied not in resolved:
                    resolved.append(copied)
                continue

            # Legacy/unknown path: keep as-is so existing references do not get dropped unexpectedly.
            normalized = str(path)
            if normalized not in resolved:
                resolved.append(normalized)

        return resolved

    def _copy_attachment_to_store(self, source: Path, anchor_day: date) -> str:
        day_dir = self.attachments_root / f"{anchor_day.year:04d}" / f"{anchor_day.month:02d}" / f"{anchor_day.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)
        safe_name = source.name.replace(" ", "_")
        target_name = f"{datetime.now().strftime('%H%M%S')}_{uuid4().hex[:8]}_{safe_name}"
        target = day_dir / target_name
        shutil.copy2(source, target)
        return str(target.relative_to(self.attachments_root).as_posix())

    def _cleanup_unused_attachments(self, candidates: list[str], current_entry_id: int | None) -> None:
        for candidate in candidates:
            path = Path(candidate)
            if not self._is_managed_attachment(path):
                continue
            rel = str(path.as_posix())
            if self._is_attachment_referenced(rel, current_entry_id):
                continue
            target = self.attachments_root / path
            try:
                if target.exists() and target.is_file():
                    target.unlink()
            except OSError:
                continue

    def _is_attachment_referenced(self, rel_path: str, exclude_entry_id: int | None) -> bool:
        rows = self.connection.execute("SELECT id, attachments_json FROM entries").fetchall()
        for row in rows:
            if exclude_entry_id is not None and row["id"] == exclude_entry_id:
                continue
            attachments = json.loads(row["attachments_json"] or "[]")
            if rel_path in attachments:
                return True
        return False

    @staticmethod
    def _is_managed_attachment(path: Path) -> bool:
        return not path.is_absolute() and len(path.parts) >= 4 and path.parts[0].isdigit()


def calendar_days(year: int, month: int) -> list[date]:
    cal = calendar.Calendar(firstweekday=6)
    days = [item for week in cal.monthdatescalendar(year, month)[:6] for item in week]
    while len(days) < 42:
        last = days[-1]
        days.append(last.fromordinal(last.toordinal() + 1))
    return days

