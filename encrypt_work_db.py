import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from taskcalendar.storage import protect_bytes

def main():
    # 1. 개발 PC에서 복구 성공한 후 추출한 평문 SQLite 파일 경로 (예: plain.db)
    plain_db_path = PROJECT_ROOT / "plain.db"
    target_enc_path = PROJECT_ROOT / "db" / "taskcalendar.db.enc"
    
    if not plain_db_path.exists():
        print(f"오류: 개발 PC에서 가져온 복구된 {plain_db_path.name} 파일이 폴더에 없습니다!")
        return

    print("평문 데이터베이스를 읽는 중...")
    plain_bytes = plain_db_path.read_bytes()

    print("업무망 PC의 Windows 키로 암호화 진행 중...")
    try:
        encrypted_bytes = protect_bytes(plain_bytes)
        
        # db 폴더 생성
        target_enc_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 파일 쓰기
        target_enc_path.write_bytes(encrypted_bytes)
        # 백업 파일도 동일하게 생성
        target_enc_path.with_suffix(".db.enc.bak").write_bytes(encrypted_bytes)
        
        print(f"성공! {target_enc_path} 경로에 암호화 복원이 완료되었습니다.")
        print("이제 캘린더 프로그램을 실행하시면 됩니다.")
    except Exception as e:
        print(f"암호화 실패: {e}")

if __name__ == "__main__":
    main()
