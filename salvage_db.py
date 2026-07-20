import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from taskcalendar.storage import unprotect_bytes
import sqlite3

def find_latest_corrupt() -> Path:
    db_dir = PROJECT_ROOT / "db"
    candidates = sorted(db_dir.glob("taskcalendar.db.enc.corrupt.*"), reverse=True)
    if not candidates:
        raise FileNotFoundError("db 폴더 내에 .corrupt.* 파일이 없습니다.")
    return candidates[0]

def main():
    try:
        corrupt_path = find_latest_corrupt()
        print(f"가장 최근의 손상 파일 발견: {corrupt_path}")
    except Exception as e:
        print(f"오류: {e}")
        return

    raw = corrupt_path.read_bytes()
    try:
        plain = unprotect_bytes(raw)
        print("1. 복호화에 성공했습니다!")
    except Exception as e:
        print(f"1. 복호화 실패: {e}")
        return

    # 평문 파일로 저장
    out_plain_path = PROJECT_ROOT / "plain_corrupt.db"
    out_plain_path.write_bytes(plain)
    print(f"2. 복호화된 평문 파일을 {out_plain_path.name} 로 저장했습니다.")

    # SQLite 구조 정밀 검사
    print("3. 데이터베이스 내부 검사 시작...")
    conn = sqlite3.connect(str(out_plain_path))
    try:
        cursor = conn.cursor()
        
        # 무결성 검사
        cursor.execute("PRAGMA integrity_check")
        integrity = cursor.fetchall()
        print(f"   - 무결성 검사 결과: {integrity}")

        # 스키마 버전
        cursor.execute("PRAGMA schema_version")
        schema_ver = cursor.fetchone()[0]
        print(f"   - 스키마 버전: {schema_ver}")

        # 테이블 목록 조회
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"   - 발견된 테이블 목록: {tables}")

        for table in tables:
            try:
                cursor.execute(f"SELECT count(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"     * 테이블 [{table}]: 데이터 {count}건 존재")
            except Exception as t_err:
                print(f"     * 테이블 [{table}] 조회 실패: {t_err}")

    except Exception as db_err:
        print(f"데이터베이스 구조 오류: {db_err}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
