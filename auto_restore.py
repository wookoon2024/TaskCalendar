import ctypes
from ctypes import wintypes
import os
import sqlite3
import shutil
from pathlib import Path

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

def check_db_validity(plain_bytes: bytes) -> int:
    """Returns the number of entries if valid SQLite DB, else -1."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(plain_bytes)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM entries")
        count = cur.fetchone()[0]
        return count
    except Exception:
        return -1
    finally:
        conn.close()

def main():
    print("==================================================")
    print("      TaskCalendar 데이터베이스 자동 복구 도구")
    print("==================================================")
    
    db_dir = Path("db")
    if not db_dir.exists():
        # Try finding in current directory
        db_dir = Path(".")

    # Find all candidates (.enc, .bak, .corrupt)
    candidates = list(db_dir.glob("taskcalendar.db.enc*"))
    if not candidates:
        print("db 폴더나 현재 폴더에서 데이터베이스 파일을 찾을 수 없습니다.")
        input("\n엔터를 누르면 종료합니다...")
        return

    valid_dbs = []
    print("\n[1] 파일 스캔 및 복호화 테스트 진행 중...\n")
    
    for path in candidates:
        raw = path.read_bytes()
        
        # Try as decrypted plain SQLite first
        count = check_db_validity(raw)
        if count >= 0:
            valid_dbs.append((path, raw, count, "평문 (암호화 없음)"))
            continue
            
        # Try decrypting
        try:
            plain = unprotect_bytes(raw)
            count = check_db_validity(plain)
            if count >= 0:
                valid_dbs.append((path, plain, count, "암호화 복원 성공"))
            else:
                print(f"❌ {path.name}: 복호화는 되었으나 내부 데이터베이스가 깨져 있습니다.")
        except Exception:
            print(f"❌ {path.name}: 복호화 실패 (계정 키 불일치 또는 파일 손상)")

    if not valid_dbs:
        print("\n실패: 복구 가능한 유효한 데이터베이스 파일을 찾지 못했습니다.")
        input("\n엔터를 누르면 종료합니다...")
        return

    print("\n[2] 복구 가능한 대상 목록:")
    for idx, (path, plain, count, status) in enumerate(valid_dbs, 1):
        print(f"  [{idx}] 파일명: {path.name}")
        print(f"      - 상태: {status}")
        print(f"      - 복구 가능한 일정/메모: {count}건")
        print("-" * 50)

    try:
        selection = input("\n복구할 번호를 입력하세요 (취소하려면 엔터): ").strip()
        if not selection:
            print("복구를 취소했습니다.")
            return
        
        sel_idx = int(selection) - 1
        if sel_idx < 0 or sel_idx >= len(valid_dbs):
            print("잘못된 번호입니다.")
            return
    except ValueError:
        print("잘못된 입력입니다.")
        return

    selected_path, plain_bytes, count, _ = valid_dbs[sel_idx]
    
    print(f"\n[3] '{selected_path.name}' 파일 복구 진행 중...")
    
    try:
        # Re-encrypt for the current machine/user session
        encrypted_bytes = protect_bytes(plain_bytes)
        
        # Overwrite db.enc and db.enc.bak
        target_db = Path("db/taskcalendar.db.enc")
        target_bak = Path("db/taskcalendar.db.enc.bak")
        
        # Backup existing just in case
        if target_db.exists():
            shutil.copy2(target_db, target_db.with_name("taskcalendar.db.enc.backup_before_recovery"))
            
        target_db.parent.mkdir(parents=True, exist_ok=True)
        target_db.write_bytes(encrypted_bytes)
        target_bak.write_bytes(encrypted_bytes)
        
        print("\n✨ 성공적으로 복구가 완료되었습니다! ✨")
        print(f"일정/메모 {count}건이 복원되었습니다. 이제 캘린더 프로그램을 실행해 주세요.")
    except Exception as e:
        print(f"\n복구 적용 실패: {e}")
        
    input("\n엔터를 누르면 종료합니다...")

if __name__ == "__main__":
    main()
