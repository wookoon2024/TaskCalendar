from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path


def backup_to_zip(db_path: Path, attachments_dir: Path, zip_filepath: Path) -> None:
    """Compresses the encrypted database file and attachments directory into a single zip file."""
    with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
        if db_path.exists():
            zipf.write(db_path, arcname=db_path.name)
        if attachments_dir.exists():
            for file in attachments_dir.rglob("*"):
                if file.is_file():
                    arcname = Path("attachments") / file.relative_to(attachments_dir)
                    zipf.write(file, arcname=arcname.as_posix())


def restore_from_zip(zip_filepath: Path, db_path: Path, attachments_dir: Path) -> None:
    """Verifies and extracts database and attachments from zip backup, replacing existing files safely."""
    with zipfile.ZipFile(zip_filepath, "r") as zipf:
        names = zipf.namelist()
        if "taskcalendar.db.enc" not in names:
            raise ValueError("올바른 백업 ZIP 파일이 아닙니다. (taskcalendar.db.enc 파일 누락)")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with zipfile.ZipFile(zip_filepath, "r") as zipf:
            zipf.extractall(tmp_path)

        extracted_db = tmp_path / "taskcalendar.db.enc"
        extracted_attachments = tmp_path / "attachments"

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


def run_auto_backup_db(db_path: Path, backup_dir: Path, interval_days: int, keep_count: int, last_backup_iso: str) -> str | None:
    """
    Checks if a backup is due based on interval_days and last_backup_iso.
    If due, creates a copy of the database file in backup_dir, rotates old backups,
    and returns the new backup ISO timestamp. Otherwise, returns None.
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

    # Rotate old backups
    if keep_count > 0:
        try:
            backups = sorted(backup_dir.glob("taskcalendar_backup_*.db.enc"))
            if len(backups) > keep_count:
                to_delete = backups[:-keep_count]
                for file_to_del in to_delete:
                    file_to_del.unlink(missing_ok=True)
        except Exception:
            pass  # Ignore rotation errors to not crash startup

    return now.isoformat()
