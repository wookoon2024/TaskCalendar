from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path


def backup_to_zip(
    db_path: Path,
    attachments_dir: Path,
    zip_filepath: Path,
    settings_dict: dict[str, str] | None = None,
    plain_db_bytes: bytes | None = None,
) -> None:
    """Compresses the database file, portable plain SQLite bytes, companion settings, and attachments into a single zip file."""
    with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
        if db_path.exists():
            zipf.write(db_path, arcname=db_path.name)
        if plain_db_bytes:
            zipf.writestr("taskcalendar.db", plain_db_bytes)
        if settings_dict:
            zipf.writestr("settings.json", json.dumps(settings_dict, ensure_ascii=False, indent=2))
        else:
            latest_settings = db_path.parent / "backups" / "settings_latest.json"
            if latest_settings.exists():
                zipf.write(latest_settings, arcname="settings.json")
        if attachments_dir.exists():
            for file in attachments_dir.rglob("*"):
                if file.is_file():
                    arcname = Path("attachments") / file.relative_to(attachments_dir)
                    zipf.write(file, arcname=arcname.as_posix())


def restore_from_zip(zip_filepath: Path, db_path: Path, attachments_dir: Path) -> dict[str, str] | None:
    """Verifies and extracts database and attachments from zip backup, replacing existing files safely."""
    with zipfile.ZipFile(zip_filepath, "r") as zipf:
        names = zipf.namelist()
        if "taskcalendar.db.enc" not in names and "taskcalendar.db" not in names:
            raise ValueError("올바른 백업 ZIP 파일이 아닙니다. (데이터베이스 파일 누락)")

    extracted_settings: dict[str, str] | None = None
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir).resolve()
        with zipfile.ZipFile(zip_filepath, "r") as zipf:
            for member in zipf.infolist():
                dest_path = (tmp_path / member.filename).resolve()
                if not str(dest_path).startswith(str(tmp_path)):
                    raise ValueError(f"보안 위험 감지: 비정상적인 백업 파일 경로({member.filename})입니다.")
            zipf.extractall(tmp_path)

        extracted_enc = tmp_path / "taskcalendar.db.enc"
        extracted_plain = tmp_path / "taskcalendar.db"
        extracted_attachments = tmp_path / "attachments"
        extracted_settings_file = tmp_path / "settings.json"

        if extracted_settings_file.exists():
            try:
                extracted_settings = json.loads(extracted_settings_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        from taskcalendar.storage import protect_bytes, unprotect_bytes, _can_deserialize_sqlite_blob

        final_db_bytes: bytes | None = None
        # 1. Prefer unencrypted taskcalendar.db for universal cross-account and cross-machine portability
        if extracted_plain.exists():
            plain_bytes = extracted_plain.read_bytes()
            if _can_deserialize_sqlite_blob(plain_bytes):
                try:
                    final_db_bytes = protect_bytes(plain_bytes)
                except Exception:
                    final_db_bytes = plain_bytes

        # 2. Fallback to extracted_enc (legacy backups)
        if final_db_bytes is None and extracted_enc.exists():
            enc_bytes = extracted_enc.read_bytes()
            try:
                decrypted = unprotect_bytes(enc_bytes)
                if _can_deserialize_sqlite_blob(decrypted):
                    try:
                        final_db_bytes = protect_bytes(decrypted)
                    except Exception:
                        final_db_bytes = decrypted
            except Exception:
                if _can_deserialize_sqlite_blob(enc_bytes):
                    try:
                        final_db_bytes = protect_bytes(enc_bytes)
                    except Exception:
                        final_db_bytes = enc_bytes
                else:
                    final_db_bytes = enc_bytes

        if final_db_bytes is None:
            raise ValueError("임시 경로에 데이터베이스 파일 추출 및 복원에 실패했습니다.")

        db_backup_path = db_path.with_suffix(db_path.suffix + ".restore_bak")
        attachments_backup_path = attachments_dir.parent / "attachments_restore_bak"

        db_backed_up = False
        attachments_backed_up = False

        try:
            if db_path.exists():
                shutil.copy2(db_path, db_backup_path)
                db_backed_up = True

            if attachments_dir.exists():
                shutil.copytree(attachments_dir, attachments_backup_path, dirs_exist_ok=True)
                attachments_backed_up = True

            # Write restored database file
            db_path.write_bytes(final_db_bytes)
            try:
                db_path.with_suffix(db_path.suffix + ".bak").write_bytes(final_db_bytes)
            except Exception:
                pass

            # Replace attachments
            if attachments_dir.exists():
                shutil.rmtree(attachments_dir)
            attachments_dir.mkdir(parents=True, exist_ok=True)

            if extracted_attachments.exists():
                for file in extracted_attachments.rglob("*"):
                    if file.is_file():
                        rel = file.relative_to(extracted_attachments)
                        dest = attachments_dir / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file, dest)

            # Preserve extracted settings to backup directory if present
            if extracted_settings_file.exists():
                backup_dir = db_path.parent / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(extracted_settings_file, backup_dir / "settings_latest.json")
                except Exception:
                    pass

        except Exception as e:
            # Rollback
            if db_backed_up and db_backup_path.exists():
                shutil.copy2(db_backup_path, db_path)
            if attachments_backed_up and attachments_backup_path.exists():
                if attachments_dir.exists():
                    shutil.rmtree(attachments_dir)
                shutil.copytree(attachments_backup_path, attachments_dir, dirs_exist_ok=True)
            raise e
        finally:
            if db_backup_path.exists():
                db_backup_path.unlink()
            if attachments_backup_path.exists():
                shutil.rmtree(attachments_backup_path)

    return extracted_settings


def run_auto_backup_db(
    db_path: Path,
    backup_dir: Path,
    interval_days: int,
    keep_count: int,
    last_backup_iso: str,
    settings_dict: dict[str, str] | None = None,
    plain_db_bytes: bytes | None = None,
) -> str | None:
    """
    Checks if a backup is due based on interval_days and last_backup_iso.
    If due, creates a copy of the database file in backup_dir, writes companion settings JSON,
    writes fail-safe plain SQLite backup, rotates old backups, and returns the new backup ISO timestamp.
    """
    from datetime import datetime, timedelta

    if not db_path.exists():
        return None

    now = datetime.now()

    # Check interval
    check_days = max(1, interval_days)
    if last_backup_iso:
        try:
            last_time = datetime.fromisoformat(last_backup_iso)
            if now < last_time + timedelta(days=check_days):
                return None  # Not due yet
        except Exception:
            pass  # If timestamp parsing fails, proceed to backup

    # Create backup
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    backup_filename = f"taskcalendar_backup_{stamp}.db.enc"
    backup_filepath = backup_dir / backup_filename

    try:
        shutil.copy2(db_path, backup_filepath)
    except Exception:
        return None

    # Write fail-safe plain SQLite snapshot to ensure recovery across Windows account changes
    if plain_db_bytes:
        try:
            sqlite_path = backup_dir / f"taskcalendar_backup_{stamp}.sqlite.bak"
            sqlite_path.write_bytes(plain_db_bytes)
            latest_sqlite = backup_dir / "database_latest.sqlite.bak"
            latest_sqlite.write_bytes(plain_db_bytes)
        except Exception:
            pass

    # Write companion settings JSON alongside .db.enc
    if settings_dict:
        try:
            settings_json_path = backup_dir / f"taskcalendar_backup_{stamp}.settings.json"
            settings_latest_path = backup_dir / "settings_latest.json"
            json_text = json.dumps(settings_dict, ensure_ascii=False, indent=2)
            settings_json_path.write_text(json_text, encoding="utf-8")
            settings_latest_path.write_text(json_text, encoding="utf-8")
        except Exception:
            pass

    # Rotate old backups
    if keep_count > 0:
        try:
            backups = sorted(backup_dir.glob("taskcalendar_backup_*.db.enc"))
            if len(backups) > keep_count:
                to_delete = backups[:-keep_count]
                for file_to_del in to_delete:
                    file_to_del.unlink(missing_ok=True)
                    # Also remove companion .settings.json and .sqlite.bak
                    companion_json = file_to_del.with_name(
                        file_to_del.name.replace(".db.enc", ".settings.json")
                    )
                    companion_json.unlink(missing_ok=True)
                    companion_sqlite = file_to_del.with_name(
                        file_to_del.name.replace(".db.enc", ".sqlite.bak")
                    )
                    companion_sqlite.unlink(missing_ok=True)
        except Exception:
            pass

    return now.isoformat()
