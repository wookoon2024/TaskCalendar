from __future__ import annotations

import calendar
import ctypes
import json
import logging
import shutil
import sqlite3
import time
import traceback
from ctypes import wintypes
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from taskcalendar.models import AlertType, CalendarEntry, DaySummary, EntryType, RecurrenceType, Alarm
from taskcalendar.lunar import get_lunar_date


CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4
logger = logging.getLogger(__name__)


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
    # CRYPTPROTECT_LOCAL_MACHINE binds encryption to the computer rather than the user login credentials,
    # preventing decryption failure when the Windows user changes their password or account credentials.
    flags = CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "TaskCalendar".encode("utf-16-le"),
        None,
        None,
        None,
        flags,
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
    # 1. Standard unprotect with UI_FORBIDDEN (handles both user-mode and machine-mode blobs seamlessly)
    if crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        try:
            return _blob_to_bytes(out_blob)
        finally:
            kernel32.LocalFree(out_blob.pbData)

    # 2. Fallback with UI_FORBIDDEN | LOCAL_MACHINE
    if crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE,
        ctypes.byref(out_blob),
    ):
        try:
            return _blob_to_bytes(out_blob)
        finally:
            kernel32.LocalFree(out_blob.pbData)

    # 3. Fallback with flags=0
    if crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        try:
            return _blob_to_bytes(out_blob)
        finally:
            kernel32.LocalFree(out_blob.pbData)

    raise ctypes.WinError()


def _can_deserialize_sqlite_blob(data: bytes) -> bool:
    try:
        temp_conn = sqlite3.connect(":memory:")
        try:
            temp_conn.deserialize(data)
            temp_conn.row_factory = sqlite3.Row
            # Deserialize can succeed with invalid bytes; force a real SQLite read path.
            temp_conn.execute("PRAGMA schema_version").fetchone()
            tables = {
                row[0]
                for row in temp_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "entries" not in tables or "settings" not in tables:
                return False
            return True
        finally:
            temp_conn.close()
    except (sqlite3.Error, Exception):
        return False


class EncryptedRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachments_root = self.db_path.parent / "attachments"
        self.attachments_root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self._load_failed = False
        self._initialize()
        try:
            if self.db_path.exists():
                self._load()
            self._ensure_columns()
            if not self.db_path.exists():
                self._seed()
                self.save()
        except sqlite3.DatabaseError as exc:
            self._log_diagnostic("database_error_during_init", f"{exc}\n{traceback.format_exc()}")
            self._recover_from_corrupt_database()

    def _log_diagnostic(self, stage: str, detail: str) -> None:
        try:
            log_path = self.db_path.parent / "taskcalendar_storage_diagnostic.log"
            if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                try:
                    content = log_path.read_text(encoding="utf-8", errors="ignore")
                    log_path.write_text("[LOG ROTATED]\n" + content[-262144:], encoding="utf-8")
                except Exception:
                    pass
            now = datetime.now().isoformat(timespec="seconds")
            db_info = "missing"
            if self.db_path.exists():
                db_info = f"exists,size={self.db_path.stat().st_size}"
            bak_path = self.db_path.with_suffix(self.db_path.suffix + ".bak")
            bak_info = "missing"
            if bak_path.exists():
                bak_info = f"exists,size={bak_path.stat().st_size}"
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"[{now}] stage={stage}\n"
                    f"db={self.db_path} ({db_info})\n"
                    f"bak={bak_path} ({bak_info})\n"
                    f"{detail.strip()}\n\n"
                )
        except Exception:
            pass

    def _recover_from_corrupt_database(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        corrupt_path = self.db_path.with_suffix(self.db_path.suffix + f".corrupt.{stamp}")
        bak_path = self.db_path.with_suffix(self.db_path.suffix + ".bak")
        corrupt_bak_path = bak_path.with_suffix(bak_path.suffix + f".corrupt.{stamp}")
        try:
            if self.db_path.exists():
                shutil.move(str(self.db_path), str(corrupt_path))
            if bak_path.exists():
                shutil.move(str(bak_path), str(corrupt_bak_path))
            self._log_diagnostic(
                "database_recovery",
                f"moved_corrupt_db={corrupt_path}\nmoved_corrupt_bak={corrupt_bak_path}",
            )
        except Exception:
            self._log_diagnostic("database_recovery_move_failed", traceback.format_exc())
        self.connection.close()
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self._initialize()
        self._ensure_columns()
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
                department TEXT NOT NULL DEFAULT '',
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
                bg_color TEXT NOT NULL DEFAULT '',
                alert_type TEXT NOT NULL DEFAULT 'none',
                alert_offset TEXT NOT NULL DEFAULT 'at_start',
                memo_group TEXT NOT NULL DEFAULT '',
                linked_work_id INTEGER DEFAULT NULL,
                linked_work_type TEXT NOT NULL DEFAULT '',
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
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS alarms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                alarm_time TEXT NOT NULL,
                repeat_days_json TEXT NOT NULL DEFAULT '[]',
                alert_offset TEXT NOT NULL DEFAULT 'at_start',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                hourly_repeat INTEGER NOT NULL DEFAULT 0,
                hourly_interval INTEGER NOT NULL DEFAULT 1,
                hourly_end_time TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS work_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER DEFAULT NULL,
                tab_id INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                title TEXT NOT NULL,
                cycle TEXT NOT NULL DEFAULT '수시',
                assignee TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                content_text TEXT NOT NULL DEFAULT '',
                content_html TEXT NOT NULL DEFAULT '',
                hwpx_blob BLOB,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (category_id) REFERENCES work_categories(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS work_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size TEXT NOT NULL DEFAULT '',
                file_type TEXT NOT NULL DEFAULT '',
                extracted_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (work_id) REFERENCES work_items(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS work_rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                attachment_id INTEGER,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                chunk_text TEXT NOT NULL,
                embedding BLOB,
                created_at TEXT NOT NULL,
                FOREIGN KEY (work_id) REFERENCES work_items(id) ON DELETE CASCADE,
                FOREIGN KEY (attachment_id) REFERENCES work_attachments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS work_entry_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(work_id, entry_id)
            );
            """
        )
        try:
            self.connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS work_rag_fts USING fts5(
                    work_id UNINDEXED,
                    title,
                    category,
                    chunk_text
                );
                """
            )
        except Exception as fts_err:
            logger.warning("FTS5 initialization notice: %s", fts_err)
        existing_alarms_cols = {row["name"] for row in self.connection.execute("PRAGMA table_info(alarms)").fetchall()}
        if "hourly_repeat" not in existing_alarms_cols:
            self.connection.execute("ALTER TABLE alarms ADD COLUMN hourly_repeat INTEGER NOT NULL DEFAULT 0")
        if "hourly_interval" not in existing_alarms_cols:
            self.connection.execute("ALTER TABLE alarms ADD COLUMN hourly_interval INTEGER NOT NULL DEFAULT 1")
        if "hourly_end_time" not in existing_alarms_cols:
            self.connection.execute("ALTER TABLE alarms ADD COLUMN hourly_end_time TEXT NOT NULL DEFAULT ''")
        existing = {row["name"] for row in self.connection.execute("PRAGMA table_info(entries)").fetchall()}
        additions = {
            "all_day": "ALTER TABLE entries ADD COLUMN all_day INTEGER NOT NULL DEFAULT 0",
            "department": "ALTER TABLE entries ADD COLUMN department TEXT NOT NULL DEFAULT ''",
            "recurrence_enabled": "ALTER TABLE entries ADD COLUMN recurrence_enabled INTEGER NOT NULL DEFAULT 0",
            "recurrence_type": "ALTER TABLE entries ADD COLUMN recurrence_type TEXT NOT NULL DEFAULT 'none'",
            "recurrence_interval": "ALTER TABLE entries ADD COLUMN recurrence_interval INTEGER NOT NULL DEFAULT 1",
            "recurrence_weekdays_json": "ALTER TABLE entries ADD COLUMN recurrence_weekdays_json TEXT NOT NULL DEFAULT '[]'",
            "recurrence_month_day": "ALTER TABLE entries ADD COLUMN recurrence_month_day INTEGER NOT NULL DEFAULT 1",
            "recurrence_month_week": "ALTER TABLE entries ADD COLUMN recurrence_month_week INTEGER NOT NULL DEFAULT 1",
            "recurrence_month_end": "ALTER TABLE entries ADD COLUMN recurrence_month_end INTEGER NOT NULL DEFAULT 0",
            "completed_dates_json": "ALTER TABLE entries ADD COLUMN completed_dates_json TEXT NOT NULL DEFAULT '[]'",
            "icon_type": "ALTER TABLE entries ADD COLUMN icon_type TEXT NOT NULL DEFAULT ''",
            "bg_color": "ALTER TABLE entries ADD COLUMN bg_color TEXT NOT NULL DEFAULT ''",
            "alert_type": "ALTER TABLE entries ADD COLUMN alert_type TEXT NOT NULL DEFAULT 'none'",
            "alert_offset": "ALTER TABLE entries ADD COLUMN alert_offset TEXT NOT NULL DEFAULT 'at_start'",
            "memo_group": "ALTER TABLE entries ADD COLUMN memo_group TEXT NOT NULL DEFAULT ''",
            "linked_work_id": "ALTER TABLE entries ADD COLUMN linked_work_id INTEGER DEFAULT NULL",
            "linked_work_type": "ALTER TABLE entries ADD COLUMN linked_work_type TEXT NOT NULL DEFAULT ''",
        }
        for name, sql in additions.items():
            if name not in existing:
                self.connection.execute(sql)

        existing_work_cat_cols = {row["name"] for row in self.connection.execute("PRAGMA table_info(work_categories)").fetchall()}
        if "parent_id" not in existing_work_cat_cols:
            try:
                self.connection.execute("ALTER TABLE work_categories ADD COLUMN parent_id INTEGER DEFAULT NULL")
            except Exception as cat_err:
                logger.warning("Could not add parent_id column to work_categories: %s", cat_err)
        if "tab_id" not in existing_work_cat_cols:
            try:
                self.connection.execute("ALTER TABLE work_categories ADD COLUMN tab_id INTEGER NOT NULL DEFAULT 1")
            except Exception as cat_err:
                logger.warning("Could not add tab_id column to work_categories: %s", cat_err)

        # Remove UNIQUE constraint from work_categories.name if present
        try:
            indexes = self.connection.execute("PRAGMA index_list(work_categories)").fetchall()
            has_unique_name = False
            for idx in indexes:
                if idx["unique"]:
                    idx_info = self.connection.execute(f"PRAGMA index_info({idx['name']})").fetchall()
                    cols = [col["name"] for col in idx_info]
                    if cols == ["name"]:
                        has_unique_name = True
                        break
            if has_unique_name:
                self.connection.execute("""
                    CREATE TABLE work_categories_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        parent_id INTEGER DEFAULT NULL,
                        tab_id INTEGER NOT NULL DEFAULT 1,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                self.connection.execute("""
                    INSERT INTO work_categories_new (id, name, parent_id, tab_id, sort_order, created_at, updated_at)
                    SELECT id, name, parent_id, tab_id, sort_order, created_at, updated_at FROM work_categories
                """)
                self.connection.execute("DROP TABLE work_categories")
                self.connection.execute("ALTER TABLE work_categories_new RENAME TO work_categories")
        except Exception as e:
            logger.warning("Could not migrate work_categories unique constraint: %s", e)

        existing_work_att_cols = {row["name"] for row in self.connection.execute("PRAGMA table_info(work_attachments)").fetchall()}
        if "folder_path" not in existing_work_att_cols:
            try:
                self.connection.execute("ALTER TABLE work_attachments ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''")
            except Exception as att_err:
                logger.warning("Could not add folder_path column to work_attachments: %s", att_err)

        # Performance indices
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_type_dates ON entries(entry_type, start_date, end_date)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_type_day ON entries(entry_type, day)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_memos ON entries(entry_type, updated_at DESC)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_alarms_lookup ON alarms(enabled, alarm_time)")
        self.connection.commit()

    def _load(self) -> None:
        candidates = [self.db_path, self.db_path.with_suffix(self.db_path.suffix + ".bak")]
        last_error: OSError | None = None

        for candidate in candidates:
            if not candidate.exists():
                continue
            raw = candidate.read_bytes()
            if not raw:
                self._log_diagnostic("load_skip_empty", f"candidate={candidate}")
                continue
            try:
                plain = unprotect_bytes(raw)
            except OSError as exc:
                last_error = exc
                self._log_diagnostic("load_unprotect_failed", f"candidate={candidate}\nerror={exc}")
                # Compatibility/recovery: older or broken files may contain plain SQLite bytes.
                if _can_deserialize_sqlite_blob(raw):
                    self.connection.close()
                    self.connection = sqlite3.connect(":memory:")
                    self.connection.row_factory = sqlite3.Row
                    self.connection.deserialize(raw)
                    self._log_diagnostic("load_plain_sqlite_fallback_ok", f"candidate={candidate}")
                    self._load_failed = False
                    return
                self._log_diagnostic("load_plain_sqlite_fallback_failed", f"candidate={candidate}")
                continue
            if _can_deserialize_sqlite_blob(plain):
                self.connection.close()
                self.connection = sqlite3.connect(":memory:")
                self.connection.row_factory = sqlite3.Row
                self.connection.deserialize(plain)
                self._log_diagnostic("load_encrypted_sqlite_ok", f"candidate={candidate}")
                self._load_failed = False
                return
            self._log_diagnostic("load_encrypted_sqlite_invalid", f"candidate={candidate}")

        # If primary and .bak candidates failed and db_path had non-empty content on disk:
        has_existing_data = self.db_path.exists() and self.db_path.stat().st_size > 0
        if has_existing_data:
            # 1. Search backups folder for newest valid auto backup (.sqlite.bak or .db.enc)
            backup_dir = self.db_path.parent / "backups"
            if backup_dir.is_dir():
                backup_files = sorted(
                    list(backup_dir.glob("*.sqlite.bak")) + list(backup_dir.glob("taskcalendar_backup_*.db.enc")),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for b_file in backup_files:
                    try:
                        b_raw = b_file.read_bytes()
                        if not b_raw:
                            continue
                        b_plain = None
                        if _can_deserialize_sqlite_blob(b_raw):
                            b_plain = b_raw
                        else:
                            try:
                                b_plain = unprotect_bytes(b_raw)
                            except OSError:
                                continue
                        if b_plain and _can_deserialize_sqlite_blob(b_plain):
                            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            unreadable_copy = self.db_path.with_name(f"{self.db_path.name}.unreadable.{stamp}")
                            try:
                                shutil.copy2(self.db_path, unreadable_copy)
                            except Exception:
                                pass
                            self.connection.close()
                            self.connection = sqlite3.connect(":memory:")
                            self.connection.row_factory = sqlite3.Row
                            self.connection.deserialize(b_plain)

                            # Restore companion settings if available
                            companion_json = b_file.with_name(
                                b_file.name.replace(".db.enc", ".settings.json").replace(".sqlite.bak", ".settings.json")
                            )
                            if not companion_json.exists():
                                companion_json = backup_dir / "settings_latest.json"
                            if companion_json.exists():
                                try:
                                    s_data = json.loads(companion_json.read_text(encoding="utf-8"))
                                    if isinstance(s_data, dict):
                                        for k, v in s_data.items():
                                            self.set_setting(k, str(v))
                                except Exception:
                                    pass

                            self._log_diagnostic(
                                "load_recovered_from_auto_backup",
                                f"recovered_from={b_file}\npreserved_unreadable={unreadable_copy}",
                            )
                            self._load_failed = False
                            # Save immediately with machine-level encryption
                            self.save()
                            return
                    except Exception as b_exc:
                        self._log_diagnostic("load_backup_candidate_failed", f"candidate={b_file}\nerror={b_exc}")

            # 2. No backup could be recovered: preserve unreadable file and PREVENT empty overwrite
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unreadable_copy = self.db_path.with_name(f"{self.db_path.name}.unreadable.{stamp}")
            try:
                shutil.copy2(self.db_path, unreadable_copy)
            except Exception:
                pass
            self._load_failed = True
            self._log_diagnostic(
                "load_failed_preventing_overwrite",
                f"preserved_unreadable={unreadable_copy}\nlast_error={last_error}",
            )
        else:
            self._load_failed = False
            if last_error is not None:
                self._log_diagnostic("load_no_usable_candidate", f"last_error={last_error}")
            else:
                self._log_diagnostic("load_no_usable_candidate", "no_candidate_error")

    def save(self) -> None:
        self.connection.commit()
        encrypted = protect_bytes(self.connection.serialize())
        if getattr(self, "_load_failed", False):
            # Guard against data destruction: if an existing database failed decryption,
            # do NOT overwrite it with empty memory state!
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            emergency_path = self.db_path.with_name(f"{self.db_path.name}.emergency_{stamp}.db.enc")
            try:
                emergency_path.write_bytes(encrypted)
            except Exception:
                pass
            self._log_diagnostic(
                "save_blocked_due_to_load_failure",
                f"Refused to overwrite {self.db_path}. Saved new session to {emergency_path}",
            )
            return

        tmp_path = self.db_path.with_suffix(self.db_path.suffix + ".tmp")
        bak_path = self.db_path.with_suffix(self.db_path.suffix + ".bak")
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                tmp_path.write_bytes(encrypted)
                if self.db_path.exists():
                    shutil.copy2(self.db_path, bak_path)
                tmp_path.replace(self.db_path)
                last_exc = None
                break
            except OSError as exc:
                # Windows search indexers / AV scanners can hold the file briefly.
                # Retry instead of silently dropping the edit (pythonw.exe has no console,
                # so an unhandled exception here would vanish without a trace).
                last_exc = exc
                time.sleep(0.15 * (attempt + 1))
        if last_exc is not None:
            self._log_diagnostic(
                "save_write_failed",
                f"db={self.db_path}\nbytes={len(encrypted)}\n{last_exc}\n{traceback.format_exc()}",
            )
            logger.error("repository.save failed for %s: %s", self.db_path, last_exc)
            raise last_exc
        try:
            entry_count = self.connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        except Exception:
            entry_count = -1
        # Log only when the entry count changes: that is exactly when new data
        # (or a deletion) must reach the file, so a missing entry is visible here.
        if entry_count != getattr(self, "_last_logged_entry_count", None):
            self._last_logged_entry_count = entry_count
            logger.info(
                "repository.save entries=%s bytes=%s -> %s",
                entry_count,
                len(encrypted),
                self.db_path,
            )

    def reload_database(self) -> None:
        if self.db_path.exists():
            self._load()
            self._ensure_columns()

    def vacuum(self) -> int:
        """Run VACUUM to defragment and reclaim free pages, then save to disk. Returns size reduction in bytes."""
        old_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        try:
            self.connection.execute("VACUUM")
            self.connection.commit()
            self.save()
            new_size = self.db_path.stat().st_size if self.db_path.exists() else 0
            return max(0, old_size - new_size)
        except Exception as exc:
            self._log_diagnostic("vacuum_failed", str(exc))
            return 0

    def cleanup_orphan_attachments(self) -> tuple[int, int]:
        """Scan attachments folder and delete unreferenced files. Returns (deleted_count, freed_bytes)."""
        if not self.attachments_root.exists():
            return (0, 0)

        rows = self.connection.execute("SELECT attachments_json FROM entries").fetchall()
        referenced = set()
        for row in rows:
            for item in json.loads(row["attachments_json"] or "[]"):
                referenced.add(item)

        deleted_count = 0
        freed_bytes = 0

        for file_path in self.attachments_root.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                rel_posix = str(file_path.relative_to(self.attachments_root).as_posix())
            except ValueError:
                continue
            # 업무(Work Manager) 전용 첨부파일 디렉토리는 work_attachments 테이블에서 관리하므로 제외
            if rel_posix.startswith("work/"):
                continue
            if rel_posix not in referenced:
                try:
                    fsize = file_path.stat().st_size
                    file_path.unlink()
                    deleted_count += 1
                    freed_bytes += fsize
                except OSError:
                    pass

        # Clean empty directories (work/ 디렉토리 보존)
        for dir_path in sorted(self.attachments_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            try:
                rel_dir = str(dir_path.relative_to(self.attachments_root).as_posix())
                if rel_dir == "work" or rel_dir.startswith("work/"):
                    continue
            except ValueError:
                pass
            if dir_path.is_dir() and not any(dir_path.iterdir()):
                try:
                    dir_path.rmdir()
                except OSError:
                    pass

        return (deleted_count, freed_bytes)

    def maybe_auto_vacuum(self) -> bool:
        """Check if DB fragmentation warrants an automatic vacuum (freelist >= 30 pages and 7 days elapsed).
        Executes silently in the background without blocking the user."""
        try:
            last_ts_str = self.get_setting("last_auto_vacuum_ts") or "0"
            try:
                last_ts = float(last_ts_str)
            except ValueError:
                last_ts = 0.0
            now = time.time()
            if now - last_ts < 7 * 86400:
                return False

            row_free = self.connection.execute("PRAGMA freelist_count").fetchone()
            free_pages = row_free[0] if row_free else 0

            row_total = self.connection.execute("PRAGMA page_count").fetchone()
            total_pages = row_total[0] if row_total else 1

            if free_pages < 30 and (free_pages / max(1, total_pages)) < 0.15:
                return False

            self.vacuum()
            self.cleanup_orphan_attachments()
            self.set_setting("last_auto_vacuum_ts", str(now))
            self.save()
            return True
        except Exception as exc:
            self._log_diagnostic("auto_vacuum_failed", str(exc))
            return False

    def get_day_order(self, day: date) -> list[int]:
        key = f"day_order_{day.isoformat()}"
        val = self.get_setting(key)
        if not val:
            return []
        try:
            return [int(x) for x in json.loads(val) if int(x) > 0]
        except Exception:
            return []

    def get_day_orders_bulk(self, days: set[date]) -> dict[date, list[int]]:
        valid_days = {d for d in days if d != date.min}
        if not valid_days:
            return {}
        key_map = {f"day_order_{d.isoformat()}": d for d in valid_days}
        placeholders = ",".join("?" for _ in key_map)
        rows = self.connection.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            list(key_map.keys()),
        ).fetchall()
        result: dict[date, list[int]] = {}
        for row in rows:
            day = key_map.get(row["key"])
            if day and row["value"]:
                try:
                    result[day] = [int(x) for x in json.loads(row["value"]) if int(x) > 0]
                except Exception:
                    pass
        return result

    def set_day_order(self, day: date, ids: list[int]) -> None:
        key = f"day_order_{day.isoformat()}"
        clean: list[int] = []
        seen: set[int] = set()
        for x in ids:
            ix = int(x)
            if ix > 0 and ix not in seen:
                seen.add(ix)
                clean.append(ix)
        self.set_setting(key, json.dumps(clean, ensure_ascii=False))

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
        if not row:
            return default
        val = row["value"]
        if val is None or (isinstance(val, str) and not val.strip() and default):
            return default
        return val

    def get_all_settings(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def set_all_settings(self, settings: dict[str, str]) -> None:
        for key, value in settings.items():
            self.connection.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
        self.connection.commit()

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
            getattr(entry, "department", "") or "",
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
            entry.bg_color,
            entry.alert_type.value,
            entry.alert_offset,
            getattr(entry, "memo_group", "") or "",
            getattr(entry, "linked_work_id", None),
            getattr(entry, "linked_work_type", "") or "",
        )
        if entry.entry_id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO entries(
                    entry_type, title, description, day, start_date, end_date,
                    start_time, end_time, all_day, assignee, department, status, attachments_json,
                    recurrence_enabled, recurrence_type, recurrence_interval,
                    recurrence_weekdays_json, recurrence_month_day, recurrence_month_week, recurrence_month_end, completed_dates_json, icon_type, bg_color, alert_type, alert_offset, memo_group, linked_work_id, linked_work_type, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values + (now, now),
            )
            entry.entry_id = int(cursor.lastrowid)
            entry.created_at = datetime.fromisoformat(now)
            entry.updated_at = datetime.fromisoformat(now)
        else:
            if entry.created_at:
                self.connection.execute(
                    """
                    UPDATE entries
                    SET entry_type=?, title=?, description=?, day=?, start_date=?, end_date=?,
                        start_time=?, end_time=?, all_day=?, assignee=?, department=?, status=?, attachments_json=?,
                        recurrence_enabled=?, recurrence_type=?, recurrence_interval=?,
                        recurrence_weekdays_json=?, recurrence_month_day=?, recurrence_month_week=?, recurrence_month_end=?, completed_dates_json=?, icon_type=?, bg_color=?, alert_type=?, alert_offset=?, memo_group=?, linked_work_id=?, linked_work_type=?, created_at=?, updated_at=?
                    WHERE id = ?
                    """,
                    values + (entry.created_at.isoformat(timespec="seconds"), now, entry.entry_id),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE entries
                    SET entry_type=?, title=?, description=?, day=?, start_date=?, end_date=?,
                        start_time=?, end_time=?, all_day=?, assignee=?, department=?, status=?, attachments_json=?,
                        recurrence_enabled=?, recurrence_type=?, recurrence_interval=?,
                        recurrence_weekdays_json=?, recurrence_month_day=?, recurrence_month_week=?, recurrence_month_end=?, completed_dates_json=?, icon_type=?, bg_color=?, alert_type=?, alert_offset=?, memo_group=?, linked_work_id=?, linked_work_type=?, updated_at=?
                    WHERE id = ?
                    """,
                    values + (now, entry.entry_id),
                )
        self._cleanup_unused_attachments(previous_attachments, entry.entry_id, entry.attachments)
        self.connection.commit()
        return entry

    save_entry = upsert_entry

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
        needed_days = {item.day for item in items if item.day and item.day != date.min}
        bulk_orders = self.get_day_orders_bulk(needed_days)
        day_orders: dict[date, dict[int, int]] = {
            d: {eid: idx for idx, eid in enumerate(bulk_orders.get(d, []))}
            for d in needed_days
        }
        items.sort(
            key=lambda item: (
                (item.day or date.min),
                day_orders.get(item.day or date.min, {}).get(item.entry_id or 0, 999999),
                0 if item.entry_type == EntryType.TASK else 1,
                item.start_time,
                item.entry_id or 0,
            )
        )
        return items

    def list_entries_for_day(self, target_day: date) -> list[CalendarEntry]:
        rows = self.connection.execute("SELECT * FROM entries WHERE entry_type IN (?, ?)", (EntryType.SCHEDULE.value, EntryType.TASK.value)).fetchall()
        items: list[CalendarEntry] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if self._occurs_on(entry, target_day):
                items.append(replace(entry, day=target_day, source_entry_id=entry.entry_id))
        order_map = {eid: idx for idx, eid in enumerate(self.get_day_order(target_day))}
        if order_map:
            items.sort(
                key=lambda item: (
                    order_map.get(item.entry_id, 999999),
                    0 if item.entry_type == EntryType.TASK else 1,
                    item.start_time,
                    item.entry_id or 0,
                )
            )
        else:
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

    def list_entries_for_work(self, work_id: int | None = None, work_title: str = "") -> list[CalendarEntry]:
        """특정 업무(ID 또는 제목)와 연결된 캘린더 일정 목록 반환"""
        clauses: list[str] = []
        params: list[object] = []
        if work_id is not None and int(work_id) > 0:
            w_id = int(work_id)
            clauses.append("(linked_work_id = ? AND linked_work_type = 'work')")
            params.append(w_id)
            clauses.append("id IN (SELECT entry_id FROM work_entry_links WHERE work_id = ?)")
            params.append(w_id)
        if work_title:
            cleaned = work_title.strip()
            if cleaned:
                # [업무] 또는 [문서] 접두어가 붙어있는 경우와 순수 제목 매칭
                clean_core = cleaned.replace("[문서]", "").replace("[업무]", "").strip()
                clauses.append("(title = ? OR title = ? OR title = ? OR title = ?)")
                params.extend([cleaned, clean_core, f"[문서] {clean_core}", f"[업무] {clean_core}"])
        if not clauses:
            return []
        sql = f"""
            SELECT * FROM entries
            WHERE entry_type = 'schedule' AND ({' OR '.join(clauses)})
            ORDER BY COALESCE(start_date, day) ASC, id ASC
        """
        rows = self.connection.execute(sql, tuple(params)).fetchall()
        # 중복 entry_id 제거
        seen_ids = set()
        result: list[CalendarEntry] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.entry_id not in seen_ids:
                seen_ids.add(entry.entry_id)
                result.append(entry)
        return result

    def link_entries_to_works(self, work_ids: list[int], entry_ids: list[int]) -> None:
        """선택한 업무(work_id)들과 문서/일정(entry_id)들을 다대다 연결"""
        now = datetime.now().isoformat()
        clean_work_ids = [int(w) for w in work_ids if w and int(w) > 0]
        clean_entry_ids = [int(e) for e in entry_ids if e and int(e) > 0]
        if not clean_work_ids or not clean_entry_ids:
            return

        for w_id in clean_work_ids:
            for e_id in clean_entry_ids:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO work_entry_links (work_id, entry_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (w_id, e_id, now),
                )

        # 기존 단일 컬럼 호환성을 위해 첫 번째 work_id를 linked_work_id로 동기화
        for e_id in clean_entry_ids:
            row = self.connection.execute("SELECT linked_work_id FROM entries WHERE id = ?", (e_id,)).fetchone()
            if row and not row["linked_work_id"]:
                self.connection.execute(
                    "UPDATE entries SET linked_work_id = ?, linked_work_type = 'work' WHERE id = ?",
                    (clean_work_ids[0], e_id),
                )
        self.save()

    def unlink_entry_from_work(self, work_id: int, entry_id: int) -> None:
        """특정 업무와 문서/일정의 연결 해제"""
        self.connection.execute(
            "DELETE FROM work_entry_links WHERE work_id = ? AND entry_id = ?",
            (int(work_id), int(entry_id)),
        )
        # 만약 entries.linked_work_id가 이 work_id였으면 다른 연결된 work_id로 변경하거나 NULL로 초기화
        other = self.connection.execute(
            "SELECT work_id FROM work_entry_links WHERE entry_id = ? LIMIT 1",
            (int(entry_id),),
        ).fetchone()
        new_wid = other["work_id"] if other else None
        new_type = "work" if new_wid else ""
        self.connection.execute(
            "UPDATE entries SET linked_work_id = ?, linked_work_type = ? WHERE id = ? AND linked_work_id = ?",
            (new_wid, new_type, int(entry_id), int(work_id)),
        )
        self.save()

    def set_entry_work_links(self, entry_id: int, work_ids: list[int]) -> None:
        """단일 항목의 연결 업무 목록을 전달받은 목록으로 일괄 갱신"""
        now = datetime.now().isoformat()
        e_id = int(entry_id)
        clean_work_ids = [int(w) for w in work_ids if w and int(w) > 0]

        self.connection.execute("DELETE FROM work_entry_links WHERE entry_id = ?", (e_id,))
        for w_id in clean_work_ids:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO work_entry_links (work_id, entry_id, created_at)
                VALUES (?, ?, ?)
                """,
                (w_id, e_id, now),
            )
        primary_wid = clean_work_ids[0] if clean_work_ids else None
        primary_type = "work" if primary_wid else ""
        self.connection.execute(
            "UPDATE entries SET linked_work_id = ?, linked_work_type = ? WHERE id = ?",
            (primary_wid, primary_type, e_id),
        )
        self.save()

    def list_linked_works_for_entry(self, entry_id: int) -> list[int]:
        """특정 항목에 연결된 모든 work_id 목록 반환"""
        rows = self.connection.execute(
            "SELECT work_id FROM work_entry_links WHERE entry_id = ?",
            (int(entry_id),),
        ).fetchall()
        w_ids = [r["work_id"] for r in rows]
        if not w_ids:
            # fallback to legacy column
            row = self.connection.execute("SELECT linked_work_id FROM entries WHERE id = ?", (int(entry_id),)).fetchone()
            if row and row["linked_work_id"]:
                w_ids = [int(row["linked_work_id"])]
        return w_ids

    def list_linked_entries_for_work(self, work_id: int | None = None, entry_type: str = "task") -> list[CalendarEntry]:
        """특정 업무(work_id)에 연결된 문서(task) 또는 일정(schedule) 목록 반환"""
        if not work_id or int(work_id) <= 0:
            return []
        w_id = int(work_id)
        sql = """
            SELECT DISTINCT e.* FROM entries e
            LEFT JOIN work_entry_links wel ON e.id = wel.entry_id
            WHERE e.entry_type = ? AND (wel.work_id = ? OR (e.linked_work_id = ? AND e.linked_work_type = 'work'))
            ORDER BY COALESCE(e.start_date, e.day, e.created_at) DESC, e.id DESC
        """
        rows = self.connection.execute(sql, (entry_type, w_id, w_id)).fetchall()
        seen = set()
        results = []
        for r in rows:
            entry = self._row_to_entry(r)
            if entry.entry_id not in seen:
                seen.add(entry.entry_id)
                results.append(entry)
        return results

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
        if entry.recurrence_type == RecurrenceType.LUNAR_YEARLY:
            if target_day.year < anchor.year:
                return False
            lunar_info = get_lunar_date(target_day)
            if lunar_info is None:
                return False
            anchor_lunar = get_lunar_date(anchor)
            req_lunar_month = entry.recurrence_month_day if (entry.recurrence_month_day and entry.recurrence_month_day > 0) else (anchor_lunar.month if anchor_lunar else anchor.month)
            req_lunar_day = entry.recurrence_month_week if (entry.recurrence_month_week and entry.recurrence_month_week > 0) else (anchor_lunar.day if anchor_lunar else anchor.day)
            req_is_leap = bool(entry.recurrence_month_end)
            if lunar_info.month != req_lunar_month or lunar_info.day != req_lunar_day:
                return False
            if req_is_leap:
                return lunar_info.is_leap
            return not lunar_info.is_leap
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
            department=row["department"] if "department" in row.keys() and row["department"] else "",
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
            bg_color=row["bg_color"] or "",
            alert_type=AlertType(row["alert_type"] or "none"),
            alert_offset=row["alert_offset"] or "at_start",
            memo_group=row["memo_group"] if "memo_group" in row.keys() and row["memo_group"] else "",
            linked_work_id=row["linked_work_id"] if "linked_work_id" in row.keys() else None,
            linked_work_type=row["linked_work_type"] if "linked_work_type" in row.keys() and row["linked_work_type"] else "",
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

    def _cleanup_unused_attachments(self, candidates: list[str], current_entry_id: int | None, current_attachments: list[str] | None = None) -> None:
        current_set = set(current_attachments or [])
        for candidate in candidates:
            if candidate in current_set:
                continue
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

    @staticmethod
    def _row_to_alarm(row: sqlite3.Row) -> Alarm:
        return Alarm(
            alarm_id=row["id"],
            title=row["title"],
            start_date=date.fromisoformat(row["start_date"]) if row["start_date"] else None,
            end_date=date.fromisoformat(row["end_date"]) if row["end_date"] else None,
            alarm_time=row["alarm_time"] or "",
            repeat_days=json.loads(row["repeat_days_json"] or "[]"),
            alert_offset=row["alert_offset"] or "at_start",
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            hourly_repeat=bool(row["hourly_repeat"]),
            hourly_interval=int(row["hourly_interval"]),
            hourly_end_time=row["hourly_end_time"] or "",
        )

    def upsert_alarm(self, alarm: Alarm) -> Alarm:
        now = datetime.now().isoformat(timespec="seconds")
        values = (
            alarm.title,
            alarm.start_date.isoformat() if alarm.start_date else None,
            alarm.end_date.isoformat() if alarm.end_date else None,
            alarm.alarm_time,
            json.dumps(alarm.repeat_days, ensure_ascii=False),
            alarm.alert_offset,
            int(alarm.enabled),
            int(alarm.hourly_repeat),
            alarm.hourly_interval,
            alarm.hourly_end_time,
        )
        if alarm.alarm_id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO alarms (
                    title, start_date, end_date, alarm_time, repeat_days_json,
                    alert_offset, enabled, hourly_repeat, hourly_interval, hourly_end_time,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values + (now, now),
            )
            alarm.alarm_id = int(cursor.lastrowid)
            alarm.created_at = datetime.fromisoformat(now)
            alarm.updated_at = datetime.fromisoformat(now)
        else:
            self.connection.execute(
                """
                UPDATE alarms
                SET title=?, start_date=?, end_date=?, alarm_time=?, repeat_days_json=?,
                    alert_offset=?, enabled=?, hourly_repeat=?, hourly_interval=?, hourly_end_time=?,
                    updated_at=?
                WHERE id = ?
                """,
                values + (now, alarm.alarm_id),
            )
            alarm.updated_at = datetime.fromisoformat(now)
        self.connection.commit()
        return alarm

    def delete_alarm(self, alarm_id: int) -> None:
        self.connection.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        self.connection.commit()

    def list_alarms(self) -> list[Alarm]:
        rows = self.connection.execute("SELECT * FROM alarms ORDER BY alarm_time ASC, id ASC").fetchall()
        return [self._row_to_alarm(row) for row in rows]

    def get_alarm(self, alarm_id: int) -> Alarm | None:
        row = self.connection.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
        return self._row_to_alarm(row) if row else None

    def list_memo_groups(self) -> list[dict]:
        raw = self.get_setting("memo_groups_v1", "[]")
        try:
            groups = json.loads(raw)
            return groups if isinstance(groups, list) else []
        except Exception:
            return []

    def get_memo_group(self, group_id: str) -> dict | None:
        for g in self.list_memo_groups():
            if g.get("id") == group_id:
                return g
        return None

    def upsert_memo_group(self, group_dict: dict, persist: bool = True) -> dict:
        groups = self.list_memo_groups()
        gid = group_dict.get("id")
        if not gid:
            import uuid
            gid = f"grp_{uuid.uuid4().hex[:8]}"
            group_dict["id"] = gid
        found = False
        for i, g in enumerate(groups):
            if g.get("id") == gid:
                groups[i] = {**g, **group_dict}
                found = True
                break
        if not found:
            groups.append(group_dict)
        self.set_setting("memo_groups_v1", json.dumps(groups, ensure_ascii=False))
        if persist:
            self.save()
        return group_dict

    def delete_memo_group(self, group_id: str, delete_memos: bool = False) -> None:
        groups = [g for g in self.list_memo_groups() if g.get("id") != group_id]
        self.set_setting("memo_groups_v1", json.dumps(groups, ensure_ascii=False))
        if delete_memos:
            for entry in self.list_memos():
                if entry.memo_group == group_id and entry.entry_id:
                    self.delete_entry(entry.entry_id)
        else:
            for entry in self.list_memos():
                if entry.memo_group == group_id:
                    entry.memo_group = ""
                    self.upsert_entry(entry)
        self.save()

    # =========================================================================
    # 업무 및 인수인계 매뉴얼 (Work Management & Knowledge Hub / RAG)
    # =========================================================================

    def list_work_categories(self, tab_id: int | None = None) -> list[dict]:
        """업무 분류 목록 조회 (parent_id 및 tab_id 포함)"""
        self._ensure_default_work_data_if_empty()
        cols = {row["name"] for row in self.connection.execute("PRAGMA table_info(work_categories)").fetchall()}
        has_tab = "tab_id" in cols
        if has_tab:
            if tab_id is not None:
                rows = self.connection.execute(
                    "SELECT id, name, parent_id, sort_order, tab_id, created_at, updated_at FROM work_categories WHERE tab_id = ? ORDER BY sort_order ASC, id ASC",
                    (int(tab_id),),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT id, name, parent_id, sort_order, tab_id, created_at, updated_at FROM work_categories ORDER BY sort_order ASC, id ASC"
                ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT id, name, parent_id, sort_order, created_at, updated_at FROM work_categories ORDER BY sort_order ASC, id ASC"
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if "tab_id" not in d:
                d["tab_id"] = 1
            results.append(d)
        return results

    def add_work_category(self, name: str, sort_order: int = 0, parent_id: int | None = None, tab_id: int = 1) -> int:
        now = datetime.now().isoformat()
        cols = {row["name"] for row in self.connection.execute("PRAGMA table_info(work_categories)").fetchall()}
        if "tab_id" in cols:
            cursor = self.connection.execute(
                "INSERT INTO work_categories (name, sort_order, parent_id, tab_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name.strip(), sort_order, parent_id, int(tab_id), now, now),
            )
        else:
            cursor = self.connection.execute(
                "INSERT INTO work_categories (name, sort_order, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name.strip(), sort_order, parent_id, now, now),
            )
        self.save()
        return cursor.lastrowid

    def update_work_category(self, cat_id: int, name: str) -> None:
        now = datetime.now().isoformat()
        self.connection.execute(
            "UPDATE work_categories SET name = ?, updated_at = ? WHERE id = ?",
            (name.strip(), now, cat_id),
        )
        self.save()

    def update_work_category_parent(self, cat_id: int, parent_id: int | None, sort_order: int) -> None:
        now = datetime.now().isoformat()
        self.connection.execute(
            "UPDATE work_categories SET parent_id = ?, sort_order = ?, updated_at = ? WHERE id = ?",
            (parent_id, sort_order, now, cat_id),
        )
        self.save()

    def update_work_category_tab(self, cat_id: int, tab_id: int) -> None:
        """카테고리 및 그 하위 카테고리들의 소속 탭(tab_id) 일괄 변경"""
        now = datetime.now().isoformat()
        cols = {row["name"] for row in self.connection.execute("PRAGMA table_info(work_categories)").fetchall()}
        if "tab_id" in cols:
            cat_ids = [cat_id]
            idx = 0
            while idx < len(cat_ids):
                curr = cat_ids[idx]
                idx += 1
                sub_rows = self.connection.execute("SELECT id FROM work_categories WHERE parent_id = ?", (curr,)).fetchall()
                for r in sub_rows:
                    if r["id"] not in cat_ids:
                        cat_ids.append(r["id"])
            placeholders = ",".join("?" for _ in cat_ids)
            self.connection.execute(
                f"UPDATE work_categories SET tab_id = ?, updated_at = ? WHERE id IN ({placeholders})",
                [int(tab_id), now, *cat_ids],
            )
            self.connection.execute(
                "UPDATE work_categories SET parent_id = NULL, updated_at = ? WHERE id = ?",
                (now, cat_id),
            )
            self.save()

    def delete_work_category(self, cat_id: int) -> None:
        # 하위 카테고리도 재귀 삭제
        sub_rows = self.connection.execute("SELECT id FROM work_categories WHERE parent_id = ?", (cat_id,)).fetchall()
        for r in sub_rows:
            self.delete_work_category(r["id"])
        self.connection.execute("DELETE FROM work_categories WHERE id = ?", (cat_id,))
        self.save()

    def update_work_item_category(self, work_id: int, category_id: int) -> None:
        """업무 항목의 소속 카테고리(분류) 변경"""
        now = datetime.now().isoformat()
        self.connection.execute(
            "UPDATE work_items SET category_id = ?, updated_at = ? WHERE id = ?",
            (category_id, now, work_id),
        )
        self.save()

    def list_work_items(self, category_id: int | None = None) -> list[dict]:
        """업무 항목 목록 조회 (첨부파일 목록 포함)"""
        self._ensure_default_work_data_if_empty()
        query = """
            SELECT w.id, w.category_id, c.name AS category_name, w.title, w.cycle, w.assignee,
                   w.deadline, w.content_text, w.content_html, w.hwpx_blob, w.sort_order,
                   w.created_at, w.updated_at
            FROM work_items w
            LEFT JOIN work_categories c ON w.category_id = c.id
        """
        params = []
        if category_id is not None:
            query += " WHERE w.category_id = ?"
            params.append(category_id)
        query += " ORDER BY w.sort_order ASC, w.id ASC"

        rows = self.connection.execute(query, params).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["attachments"] = self.list_work_attachments(d["id"])
            items.append(d)
        return items

    def get_work_item(self, work_id: int) -> dict | None:
        row = self.connection.execute(
            """
            SELECT w.id, w.category_id, c.name AS category_name, w.title, w.cycle, w.assignee,
                   w.deadline, w.content_text, w.content_html, w.hwpx_blob, w.sort_order,
                   w.created_at, w.updated_at
            FROM work_items w
            LEFT JOIN work_categories c ON w.category_id = c.id
            WHERE w.id = ?
            """,
            (work_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["attachments"] = self.list_work_attachments(work_id)
        return d

    def upsert_work_item(
        self,
        work_id: int | None,
        title: str,
        category_name: str = "일반 업무",
        category_id: int | None = None,
        cycle: str = "수시",
        assignee: str = "",
        deadline: str = "",
        content_text: str = "",
        content_html: str = "",
        hwpx_blob: bytes | None = None,
        sort_order: int = 0,
    ) -> int:
        """업무 항목 생성 또는 수정 및 RAG 인덱싱 연동"""
        now = datetime.now().isoformat()
        cat_id = category_id
        if cat_id is None:
            cat_row = self.connection.execute(
                "SELECT id FROM work_categories WHERE name = ?", (category_name.strip(),)
            ).fetchone()
            if cat_row:
                cat_id = cat_row["id"]
            else:
                cat_id = self.add_work_category(category_name.strip())

        if work_id is None or work_id <= 0:
            cursor = self.connection.execute(
                """
                INSERT INTO work_items (
                    category_id, title, cycle, assignee, deadline,
                    content_text, content_html, hwpx_blob, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cat_id, title.strip(), cycle, assignee.strip(), deadline.strip(),
                 content_text, content_html, hwpx_blob, sort_order, now, now),
            )
            work_id = cursor.lastrowid
        else:
            if hwpx_blob is not None:
                self.connection.execute(
                    """
                    UPDATE work_items SET
                        category_id = ?, title = ?, cycle = ?, assignee = ?, deadline = ?,
                        content_text = ?, content_html = ?, hwpx_blob = ?, sort_order = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (cat_id, title.strip(), cycle, assignee.strip(), deadline.strip(),
                     content_text, content_html, hwpx_blob, sort_order, now, work_id),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE work_items SET
                        category_id = ?, title = ?, cycle = ?, assignee = ?, deadline = ?,
                        content_text = ?, content_html = ?, sort_order = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (cat_id, title.strip(), cycle, assignee.strip(), deadline.strip(),
                     content_text, content_html, sort_order, now, work_id),
                )

        self._update_work_rag_index(work_id, title, category_name, content_text)
        self.save()
        return work_id

    def delete_work_item(self, work_id: int) -> None:
        self.delete_all_work_attachment_files(work_id)
        self.connection.execute("DELETE FROM work_items WHERE id = ?", (work_id,))
        self.connection.execute("DELETE FROM work_attachments WHERE work_id = ?", (work_id,))
        self.connection.execute("DELETE FROM work_rag_chunks WHERE work_id = ?", (work_id,))
        try:
            self.connection.execute("DELETE FROM work_rag_fts WHERE work_id = ?", (work_id,))
        except Exception:
            pass
        self.save()

    def get_work_attachments_dir(self, work_id: int, subfolder: str = "") -> Path:
        """업무 문서별 전용 첨부파일 디렉토리 반환 (attachments/work/work_{work_id}/{subfolder})"""
        d = self.attachments_root / "work" / f"work_{work_id}"
        if subfolder:
            clean_sub = Path(subfolder.strip().replace("\\", "/")).as_posix().strip("/")
            if clean_sub and ".." not in clean_sub:
                d = d / clean_sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def copy_work_attachment_file(self, work_id: int, source_path: Path | str, subfolder: str = "") -> tuple[str, str, str]:
        """
        외부 파일을 프로그램 내부 업무 첨부파일 보관 디렉토리에 복사
        반환값: (저장된_절대경로문자열, 파일크기문자열, 저장된파일명)
        """
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        target_dir = self.get_work_attachments_dir(work_id, subfolder)
        file_name = src.name
        dest = target_dir / file_name

        # 이름 중복 시 (1), (2) 번호 부여
        counter = 1
        base_stem = src.stem
        suffix = src.suffix
        while dest.exists() and dest.resolve() != src.resolve():
            dest = target_dir / f"{base_stem} ({counter}){suffix}"
            counter += 1

        if dest.resolve() != src.resolve():
            import shutil
            shutil.copy2(src, dest)

        size_bytes = dest.stat().st_size
        size_str = self._format_file_size(size_bytes)
        return str(dest), size_str, dest.name

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def list_work_attachments(self, work_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id, work_id, file_name, file_path, file_size, file_type, folder_path, extracted_text, created_at "
            "FROM work_attachments WHERE work_id = ? ORDER BY id ASC",
            (work_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_work_attachment(
        self,
        work_id: int,
        file_name: str,
        file_path: str,
        file_size: str,
        file_type: str = "",
        folder_path: str = "",
        extracted_text: str = "",
    ) -> int:
        now = datetime.now().isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO work_attachments (work_id, file_name, file_path, file_size, file_type, folder_path, extracted_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (work_id, file_name, file_path, file_size, file_type, folder_path, extracted_text, now),
        )
        att_id = cursor.lastrowid
        self.save()
        return att_id

    def delete_work_attachment(self, att_id: int, delete_file: bool = True) -> None:
        """첨부파일 DB 삭제 및 (선택 시) 디스크 상의 실제 보관 파일 영구 삭제"""
        row = self.connection.execute(
            "SELECT file_path FROM work_attachments WHERE id = ?", (att_id,)
        ).fetchone()
        if row and delete_file:
            path_str = row["file_path"]
            if path_str:
                try:
                    f = Path(path_str)
                    if f.exists() and f.is_file():
                        f.unlink()
                except Exception as e:
                    logger.warning(f"Could not delete physical attachment file: {path_str}: {e}")

        self.connection.execute("DELETE FROM work_attachments WHERE id = ?", (att_id,))
        self.connection.execute("DELETE FROM work_rag_chunks WHERE attachment_id = ?", (att_id,))
        self.save()

    def delete_work_attachment_folder(self, work_id: int, folder_path: str) -> None:
        """폴더 및 하위 모든 첨부파일 물리 파일 및 DB 삭제"""
        clean_sub = Path(folder_path.strip().replace("\\", "/")).as_posix().strip("/")
        folder_dir = self.get_work_attachments_dir(work_id, clean_sub)
        if folder_dir.exists():
            import shutil
            shutil.rmtree(folder_dir, ignore_errors=True)

        self.connection.execute(
            "DELETE FROM work_attachments WHERE work_id = ? AND (folder_path = ? OR folder_path LIKE ? OR (file_type = 'folder' AND file_name = ?))",
            (work_id, clean_sub, f"{clean_sub}/%", clean_sub),
        )
        self.save()

    def rename_work_attachment_folder(self, work_id: int, old_folder_path: str, new_folder_path: str) -> None:
        """첨부파일 폴더 이름 변경 및 하위 파일 경로 일괄 갱신"""
        old_clean = Path(old_folder_path.strip().replace("\\", "/")).as_posix().strip("/")
        new_clean = Path(new_folder_path.strip().replace("\\", "/")).as_posix().strip("/")

        old_dir = self.get_work_attachments_dir(work_id, old_clean)
        new_dir = self.get_work_attachments_dir(work_id, new_clean)
        if old_dir.exists() and not new_dir.exists():
            import shutil
            shutil.move(str(old_dir), str(new_dir))

        rows = self.connection.execute(
            "SELECT id, file_path, folder_path, file_name, file_type FROM work_attachments WHERE work_id = ?",
            (work_id,),
        ).fetchall()

        for r in rows:
            f_path = r["folder_path"]
            f_type = r["file_type"]
            f_name = r["file_name"]
            updated_folder = None
            if f_path == old_clean:
                updated_folder = new_clean
            elif f_path.startswith(old_clean + "/"):
                updated_folder = new_clean + f_path[len(old_clean):]

            new_file_name = f_name
            if f_type == "folder" and f_name == Path(old_clean).name:
                new_file_name = Path(new_clean).name

            if updated_folder is not None or new_file_name != f_name:
                curr_folder = updated_folder if updated_folder is not None else f_path
                new_phys_path = r["file_path"]
                if new_phys_path and str(old_dir) in new_phys_path:
                    new_phys_path = new_phys_path.replace(str(old_dir), str(new_dir))
                self.connection.execute(
                    "UPDATE work_attachments SET folder_path = ?, file_name = ?, file_path = ? WHERE id = ?",
                    (curr_folder, new_file_name, new_phys_path, r["id"]),
                )
        self.save()

    def delete_all_work_attachment_files(self, work_id: int) -> None:
        """업무 삭제 시 해당 업무에 보관된 모든 첨부파일 디렉토리 및 파일 물리 삭제"""
        work_dir = self.attachments_root / "work" / f"work_{work_id}"
        if work_dir.exists():
            import shutil
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Could not delete work attachment directory {work_dir}: {e}")

    def _update_work_rag_index(self, work_id: int, title: str, category: str, content_text: str) -> None:
        """업무 문서의 본문을 청킹하여 RAG 및 FTS 색인 테이블에 등록"""
        self.connection.execute("DELETE FROM work_rag_chunks WHERE work_id = ? AND attachment_id IS NULL", (work_id,))
        try:
            self.connection.execute("DELETE FROM work_rag_fts WHERE work_id = ?", (work_id,))
        except Exception:
            pass

        if not content_text or not content_text.strip():
            content_text = title

        chunks = []
        curr = []
        curr_len = 0
        for line in content_text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if curr_len + len(line_str) > 500:
                chunks.append("\n".join(curr))
                curr = [line_str]
                curr_len = len(line_str)
            else:
                curr.append(line_str)
                curr_len += len(line_str)
        if curr:
            chunks.append("\n".join(curr))

        if not chunks:
            chunks = [title]
        elif len(chunks) > 60:
            # 대용량 문서 색인 시 FTS 폭증 및 DB 팽창 방지
            chunks = chunks[:60]

        now = datetime.now().isoformat()
        for i, chunk in enumerate(chunks):
            self.connection.execute(
                """
                INSERT INTO work_rag_chunks (work_id, chunk_index, chunk_text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (work_id, i, chunk, now),
            )
            try:
                self.connection.execute(
                    "INSERT INTO work_rag_fts (work_id, title, category, chunk_text) VALUES (?, ?, ?, ?)",
                    (work_id, title, category, chunk),
                )
            except Exception:
                pass

    def search_work_rag(self, query: str, limit: int = 15) -> list[dict]:
        """FTS5 기반 고속 RAG/키워드 지식 검색"""
        if not query or not query.strip():
            return []
        q = query.strip()
        try:
            fts_query = " OR ".join(f'"{token}"' for token in q.split() if token)
            rows = self.connection.execute(
                """
                SELECT w.id, w.title, c.name AS category_name, w.cycle, w.assignee, snippet(work_rag_fts, 3, '<b>', '</b>', '...', 15) AS snippet
                FROM work_rag_fts f
                JOIN work_items w ON f.work_id = w.id
                LEFT JOIN work_categories c ON w.category_id = c.id
                WHERE work_rag_fts MATCH ?
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            like = f"%{q}%"
            rows = self.connection.execute(
                """
                SELECT w.id, w.title, c.name AS category_name, w.cycle, w.assignee, w.content_text AS snippet
                FROM work_items w
                LEFT JOIN work_categories c ON w.category_id = c.id
                WHERE w.title LIKE ? OR w.content_text LIKE ? OR c.name LIKE ?
                LIMIT ?
                """,
                (like, like, like, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def _ensure_default_work_data_if_empty(self) -> None:
        """초기 실행 시 빈 상태 유지 (샘플 데이터 자동 생성 안 함)"""
        return


def calendar_days(year: int, month: int) -> list[date]:
    cal = calendar.Calendar(firstweekday=6)
    days = [item for week in cal.monthdatescalendar(year, month)[:6] for item in week]
    while len(days) < 42:
        last = days[-1]
        days.append(last.fromordinal(last.toordinal() + 1))
    return days
