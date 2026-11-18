"""문서 목록 확인용 테스트 문서를 넣거나 지운다.

    python tools/seed_doc_samples.py <data 폴더>            # 넣기
    python tools/seed_doc_samples.py <data 폴더> --remove   # 지우기

<data 폴더> 는 db/taskcalendar.db.enc 가 들어 있는(또는 새로 만들) 폴더다.
넣은 문서는 제목이 '[테스트] ' 로 시작하고, 함께 만든 업무는 '테스트 업무' 분류에 들어간다.
--remove 는 그 둘(과 문서에 딸린 회신 일정)만 지운다.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from taskcalendar.models import CalendarEntry, EntryType  # noqa: E402
from taskcalendar.storage import EncryptedRepository  # noqa: E402

PREFIX = "[테스트] "
WORK_CATEGORY = "테스트 업무"

# (등록일 오프셋, 분류, 제목, 부서, 기안자, 상태, 회신기한 오프셋(None=없음), 회신 시각, 비고, 출처 URL, 관련 업무 이름들)
SAMPLES = [
    (0, "공문", "2027년도 예산 편성 기초자료 제출 요청", "기획예산과", "김민준", "등록", 3, "", "부서별 신규사업 목록과 소요예산을 서식에 맞춰 제출", "https://example.go.kr/doc/1001", ["예산 편성"]),
    (0, "민원", "도로 파손 보수 요청 민원 회신", "도로과", "이서연", "진행중", 0, "18:00", "현장 확인 후 보수 일정 안내", "", ["민원 처리"]),
    (-1, "보고", "주간 업무 추진 실적 보고", "총무과", "박지훈", "등록", 1, "", "", "", []),
    (-2, "공문", "개인정보 보호 실태 점검 계획 알림", "정보통신과", "최유나", "진행중", 7, "", "체크리스트 자체 점검 후 결과 회신", "https://example.go.kr/doc/1002", ["개인정보 점검", "보안 감사"]),
    (-3, "기안", "청사 냉난방기 교체 공사 추진", "회계과", "정도윤", "보류", None, "", "예산 확보 후 재추진 (보류 사유: 하반기 추경)", "", ["청사 관리"]),
    (-4, "공문", "재난 대비 비상연락망 정비 요청", "안전총괄과", "한지민", "등록", -2, "", "변경된 연락처 반영해서 회신 필요", "https://example.go.kr/doc/1003", []),
    (-5, "결재", "출장비 정산 결재 요청", "총무과", "윤서준", "완료", -3, "", "", "", []),
    (-6, "민원", "주차장 이용 시간 연장 건의 민원에 대한 검토 결과 회신 (관련 부서 협의 포함)", "교통행정과", "강하은", "진행중", 14, "", "관련 부서 협의 결과 반영, 회신문 초안 작성 중이며 다음 주 결재 예정. 협의 부서: 교통행정과, 시설관리과, 재무과", "https://example.go.kr/minwon/2026-1188", ["민원 처리"]),
    (-8, "예산", "추가경정예산 요구서 작성", "기획예산과", "김민준", "진행중", 2, "10:00", "", "", ["예산 편성"]),
    (-9, "일반", "직원 교육 수요 조사", "인사과", "오지우", "등록", None, "", "", "https://example.go.kr/edu/survey", []),
    (-10, "보고", "상반기 주요 사업 추진 상황 점검 결과", "기획예산과", "이서연", "완료", None, "", "점검 결과 보고 완료", "", ["예산 편성"]),
    (-12, "기타", "사무용품 구입 신청 취합", "총무과", "박지훈", "취소", None, "", "", "", []),
    (-13, "공문", "정보보안 교육 이수 결과 제출", "정보통신과", "최유나", "등록", -7, "", "미이수자 명단 포함", "https://example.go.kr/doc/1004", ["보안 감사"]),
    (-20, "민원", "가로등 고장 신고 처리 결과 회신", "도로과", "정도윤", "완료", -15, "", "", "", ["민원 처리"]),
    (-25, "기안", "청사 방역 용역 계약 체결", "회계과", "한지민", "진행중", 30, "", "", "", ["청사 관리"]),
]


def open_repo(data_dir: Path) -> EncryptedRepository:
    (data_dir / "db").mkdir(parents=True, exist_ok=True)
    return EncryptedRepository(data_dir / "db" / "taskcalendar.db.enc")


def remove(repo: EncryptedRepository) -> None:
    rows = repo.connection.execute(
        "SELECT id FROM entries WHERE entry_type = ? AND title LIKE ?",
        (EntryType.TASK.value, PREFIX + "%"),
    ).fetchall()
    ids = [int(r["id"]) for r in rows]
    for entry_id in ids:
        repo.connection.execute("DELETE FROM entries WHERE linked_doc_id = ?", (entry_id,))
        repo.connection.execute("DELETE FROM work_entry_links WHERE entry_id = ?", (entry_id,))
        repo.connection.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    cat = repo.connection.execute("SELECT id FROM work_categories WHERE name = ?", (WORK_CATEGORY,)).fetchone()
    works = 0
    if cat:
        works = repo.connection.execute("DELETE FROM work_items WHERE category_id = ?", (cat["id"],)).rowcount
        repo.connection.execute("DELETE FROM work_categories WHERE id = ?", (cat["id"],))
    repo.connection.commit()
    repo.save()
    print(f"지움: 문서 {len(ids)}건, 업무 {works}건")


def seed(repo: EncryptedRepository) -> None:
    today = date.today()
    work_ids: dict[str, int] = {}
    for sample in SAMPLES:
        for name in sample[10]:
            if name not in work_ids:
                work_ids[name] = repo.upsert_work_item(None, name, category_name=WORK_CATEGORY)

    now = datetime.now()
    for (d_off, cat, title, dept, author, status, r_off, r_time, desc, url, works) in SAMPLES:
        day = today + timedelta(days=d_off)
        body = f"{desc}\n\n출처: {url}".strip() if url else desc
        doc = CalendarEntry(
            entry_type=EntryType.TASK,
            title=PREFIX + title,
            description=body,
            day=day,
            start_date=day,
            assignee=author,
            department=dept,
            memo_group=cat,
            status=status,
            created_at=datetime.combine(day, now.time()),
        )
        if r_off is not None:
            doc.reply_due = today + timedelta(days=r_off)
            doc.reply_time = r_time
        doc = repo.upsert_entry(doc)
        if doc.reply_due:
            repo.set_reply_schedule(doc, enabled=True)
        if works:
            repo.set_entry_work_links(doc.entry_id, [work_ids[w] for w in works])
    repo.save()
    print(f"넣음: 문서 {len(SAMPLES)}건, 업무 {len(work_ids)}건 ('{WORK_CATEGORY}' 분류)")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    repo = open_repo(Path(sys.argv[1]))
    if "--remove" in sys.argv[2:]:
        remove(repo)
    else:
        remove(repo)  # 두 번 넣어도 겹치지 않게
        seed(repo)


if __name__ == "__main__":
    main()
