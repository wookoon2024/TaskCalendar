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
) -> None:
    """Compresses the encrypted database file, companion settings, and attachments into a single zip file."""
    with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
        if db_path.exists():
            zipf.write(db_path, arcname=db_path.name)
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
        if "taskcalendar.db.enc" not in names:
            raise ValueError("올바른 백업 ZIP 파일이 아닙니다. (taskcalendar.db.enc 파일 누락)")

    extracted_settings: dict[str, str] | None = None
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with zipfile.ZipFile(zip_filepath, "r") as zipf:
            zipf.extractall(tmp_path)

        extracted_db = tmp_path / "taskcalendar.db.enc"
        extracted_attachments = tmp_path / "attachments"
        extracted_settings_file = tmp_path / "settings.json"

        if extracted_settings_file.exists():
            try:
                extracted_settings = json.loads(extracted_settings_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        if not extracted_db.exists():
            raise ValueError("임시 경로에 데이터베이스 파일 추출을 실패했습니다.")

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

            # Replace database file
            shutil.copy2(extracted_db, db_path)

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
) -> str | None:
    """
    Checks if a backup is due based on interval_days and last_backup_iso.
    If due, creates a copy of the database file in backup_dir, writes companion settings JSON,
    rotates old backups, and returns the new backup ISO timestamp. Otherwise, returns None.
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
        # Ignore errors during auto-backup to not crash startup
        return None

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
                    # Also remove companion .settings.json
                    companion_json = file_to_del.with_name(
                        file_to_del.name.replace(".db.enc", ".settings.json")
                    )
                    companion_json.unlink(missing_ok=True)
        except Exception:
            pass  # Ignore rotation errors to not crash startup

    return now.isoformat()
