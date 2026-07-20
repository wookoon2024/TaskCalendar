# TaskCalendar

TaskCalendar는 Windows 데스크톱에서 사용하는 개인 일정/메모 관리 앱입니다.  
일정 반복, 첨부파일, 알림, 테마, 암호화 저장을 지원합니다.

## 주요 기능

- 일정/메모 등록 및 수정
- 반복 일정(매일, 매주, 매월, 매월 n번째 요일, 매년)
- 첨부파일 연결
- 팝업 알림
- 테마(`light`, `warm`, `dark`)
- 로컬 데이터 암호화 저장(Windows DPAPI)

## 다운로드 (실행 프로그램)

* 💾 **[Calendar.zip 다운로드 (Windows 64-bit)](https://github.com/wookoon2024/TaskCalendar/raw/main/Calendar.zip)**
  * 다운로드 후 압축을 풀고 `Calendar.exe` 파일을 실행하면 설치 없이 바로 사용할 수 있습니다.

## 실행 방법

### 소스코드 실행
```powershell
python main.py
```

### 실행 프로그램 (.exe) 직접 빌드
PyInstaller를 사용하여 단일 실행 파일을 빌드할 수 있습니다:
```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Calendar --specpath . `
  --add-data "taskcalendar/assets;taskcalendar/assets" `
  --add-data "data/holidays_kr.json;data" `
  .\main.py
```

## 프로젝트 구조

- `main.py`: 앱 실행 진입점
- `taskcalendar/app.py`: 애플리케이션 시작 로직
- `taskcalendar/qt_main_window.py`: 메인 화면 UI/동작
- `taskcalendar/qt_dialogs.py`: 일정/메모 등록 다이얼로그
- `taskcalendar/storage.py`: SQLite 저장 및 반복 계산
- `taskcalendar/models.py`: 데이터 모델 정의
- `taskcalendar/themes.py`: 테마 및 스타일

## 데이터 저장 방식

- 저장 파일: `data/taskcalendar.db.enc`
- 내부 DB: SQLite
- 보안: SQLite 바이트를 Windows DPAPI로 암호화 후 저장
- 실행 중에는 메모리 DB를 사용

## 버전 히스토리

- 변경 이력은 `CHANGELOG.md`에서 관리합니다.
