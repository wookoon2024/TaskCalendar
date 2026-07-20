import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

def main():
    plain_path = PROJECT_ROOT / "plain_corrupt.db"
    if not plain_path.exists():
        print("오류: plain_corrupt.db 파일이 없습니다. salvage_db.py를 먼저 실행해 주세요.")
        return

    data = plain_path.read_bytes()
    size = len(data)
    print(f"파일 크기: {size} 바이트")
    
    if size == 0:
        print("파일이 비어 있습니다 (0바이트).")
        return

    # 첫 100바이트 분석
    header = data[:100]
    print(f"헤더 바이너리 (첫 100자): {header}")
    
    # 텍스트 변환 시도
    try:
        text_preview = header.decode('utf-8', errors='replace')
        print(f"헤더 텍스트 프리뷰: {text_preview}")
    except Exception as e:
        print(f"디코딩 오류: {e}")

    # SQLite 표준 헤더 검사
    if data.startswith(b"SQLite format 3\x00"):
        print("-> SQLite 표준 헤더가 확인됩니다. 파일 뒷부분이 손상되었거나 잘렸을 수 있습니다.")
    else:
        print("-> SQLite 표준 헤더(SQLite format 3)가 없습니다! 데이터가 다른 포맷이거나 심하게 깨졌습니다.")

if __name__ == "__main__":
    main()
