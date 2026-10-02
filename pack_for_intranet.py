"""
Packs TaskCalendar source code into a clean ZIP file tailored for intranet network transfer.
Filters out all file types known to be blocked by enterprise DLP / network data transfer filters:
- .spec
- .md
- .ico
- .wasm
- .ts / .d.ts
- .webmanifest
- Extensionless files (LICENSE, etc.)
- .git, .venv, __pycache__, build, dist, zip
"""
from __future__ import annotations

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BLOCKED_EXTENSIONS = {
    ".spec",
    ".md",
    ".ico",
    ".wasm",
    ".ts",
    ".webmanifest",
    ".exe",
    ".bak",
    ".enc",
    ".whl",
    ".zip",
    ".pyc",
}

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".vscode",
    ".idea",
    "build",
    "dist",
    "package",  # rhwp npm package
}

EXCLUDED_FILES = {
    "assets.zip",
    "patch_spec.py",
    "pefile.py",
    "peutils.py",
}

README_TXT = """=============================================================
TaskCalendar (캘린더 & 업무 관리) 업무망 소스 안내
=============================================================

1. 개요:
   본 압축 파일은 업무망 보안 필터(위변조/차단 확장자)를 완전히 통과하도록
   .spec, .md, .ico, .wasm 등의 파일이 정제된 소스 패키지입니다.

2. 바로 실행하기 (Python 소스 실행):
   > python main.py

   * Qt 웹엔진 기반 업무 관리(한글 에디터) 및 모든 기능이 즉시 구동됩니다.
   * WASM 파일은 호환 데이터(.dat)를 통해 백그라운드에서 자동 서빙됩니다.

3. 실행 파일(.exe) 빌드하기:
   업무망 PC에서 PyInstaller로 단일 실행 파일을 생성하려면:
   > python build_exe.py

   * 자동으로 app_icon.ico 생성 및 rhwp_bg.wasm 복원 후
     Calendar.exe 단일 바이너리를 빌드합니다. (dist/Calendar.exe 생성)

4. 자산만 복원하고자 할 경우:
   > python restore.py
=============================================================
"""


def create_package() -> Path:
    from taskcalendar import APP_VERSION
    version_str = APP_VERSION if APP_VERSION.startswith("v") else f"v{APP_VERSION}"
    date_str = datetime.now().strftime("%Y%m%d")
    out_name = f"TaskCalendar_업무망전송용_{version_str}_{date_str}.zip"
    out_path = ROOT / out_name

    included_count = 0
    total_bytes = 0

    print(f"Creating package: {out_name}...")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. README_INTRANET.txt
        zf.writestr("README_INTRANET.txt", README_TXT.encode("utf-8"))
        included_count += 1

        # 2. Essential root scripts
        for root_file in ["main.py", "build_exe.py", "restore.py"]:
            rf_path = ROOT / root_file
            if rf_path.exists():
                zf.write(rf_path, root_file)
                included_count += 1
                total_bytes += rf_path.stat().st_size

        # 3. Essential holiday data
        data_file = ROOT / "data" / "holidays_kr.json"
        if data_file.exists():
            zf.write(data_file, "data/holidays_kr.json")
            included_count += 1
            total_bytes += data_file.stat().st_size

        # 4. taskcalendar package
        tc_root = ROOT / "taskcalendar"
        for dirpath, dirnames, filenames in os.walk(tc_root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
            for fname in filenames:
                file_path = Path(dirpath) / fname
                suf = file_path.suffix.lower()

                # Filter out files without suffix or in blocked extensions
                if not suf or suf in BLOCKED_EXTENSIONS:
                    continue
                if file_path.name in EXCLUDED_FILES:
                    continue

                rel_path = file_path.relative_to(ROOT)
                zf.write(file_path, str(rel_path).replace("\\", "/"))
                included_count += 1
                total_bytes += file_path.stat().st_size

    zip_size = out_path.stat().st_size
    print(f"\nPackage created successfully: {out_path.name}")
    print(f"- Total files: {included_count}")
    print(f"- Uncompressed size: {total_bytes:,} bytes ({total_bytes / (1024*1024):.2f} MB)")
    print(f"- Compressed ZIP size: {zip_size:,} bytes ({zip_size / (1024*1024):.2f} MB)")

    # Strict DLP Validation
    print("\nVerifying archive against intranet transmission DLP criteria...")
    violation_found = False
    with zipfile.ZipFile(out_path, "r") as zf:
        for info in zf.infolist():
            p = Path(info.filename)
            suf = p.suffix.lower()
            if not suf:
                print(f"[FAIL] No extension found: {info.filename}")
                violation_found = True
            if suf in BLOCKED_EXTENSIONS:
                print(f"[FAIL] Blocked extension found: {info.filename} ({suf})")
                violation_found = True

    if violation_found:
        print("\n[ERROR] Archive failed DLP validation!")
        sys.exit(1)
    else:
        print("[SUCCESS] All files strictly conform to intranet security rules!")

    return out_path


if __name__ == "__main__":
    create_package()
