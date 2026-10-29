"""업무 문서 내 법령 근거 인식, 개정 감지 및 조문 대조 모듈.

작성된 업무 내용에서 법령·조문 근거를 자동 인식하고,
해당 법령의 개정 여부를 국가법령정보센터(law.go.kr)와 연동하여 감지하며,
최신 개정 조문과의 대조 및 본문 업데이트를 지원합니다.
"""
from __future__ import annotations

import html
from dataclasses import asdict, dataclass, field
import difflib
import logging
import re
from typing import Any

from taskcalendar.law_reference import (
    LAW_DETAIL_BASE,
    get_law_article,
    get_law_detail,
    search_current_laws,
    search_laws,
)

logger = logging.getLogger(__name__)

# 일상 용어 및 법률 분야 일반 명칭 (단독 또는 구문의 끝에 올 때 법령명으로 오탐 방지)
_STOP_WORDS = {
    "방법", "기법", "문법", "어법", "용법", "화법", "작법", "공법", "농법", "연산법",
    "합법", "불법", "편법", "탈법", "준법", "위법", "적법", "입법", "사법", "행정법",
    "헌법", "민법", "형법", "상법", "소송법",
    "주정차", "단속", "조성", "방안", "대책", "계획", "지침", "매뉴얼",
}

_STOP_SUFFIXES = (
    "불법", "편법", "위법", "탈법", "합법", "적법", "준법",
    "방법", "기법", "문법", "어법", "용법", "화법", "작법", "공법", "농법",
)

_CONJUNCTIONS = {
    "및", "또는", "혹은", "등", "에", "의", "과", "와",
    "따라", "의거", "따른", "관한", "대한", "관련", "위한", "통한",
}

# 1) 따옴표로 명시된 법령명: 「개인정보 보호법」, 『지방공무원법』, "도로교통법" 등
_LAW_PATTERN_QUOTED = re.compile(
    r"[「『\"“](?P<law>[가-힣0-9\s·ㆍ]{2,35}(?:법률|법|시행령|시행규칙|규정|조례|규칙))[」』\"”]"
    r"(?:\s*제\s*(?P<art>\d+)\s*조(?:\s*의\s*(?P<sub_art>\d+))?)?"
    r"(?:\s*제\s*(?P<hang>\d+)\s*항)?",
    re.UNICODE,
)

# 2) 조문 번호(제N조)가 명시된 법령 인용: 예) 개인정보 보호법 제15조, 도로교통법 시행령 제10조의2
_LAW_PATTERN_WITH_ARTICLE = re.compile(
    r"(?<![가-힣0-9])(?P<law>[가-힣0-9]{2,15}(?:\s+[가-힣0-9]{1,10}){0,2}(?:법률|법|시행령|시행규칙|규정|조례|규칙))(?![가-힣0-9])"
    r"\s+제\s*(?P<art>\d+)\s*조(?:\s*의\s*(?P<sub_art>\d+))?"
    r"(?:\s*제\s*(?P<hang>\d+)\s*항)?",
    re.UNICODE,
)

# 3) 따옴표나 조문이 없는 단독 고유 명칭 (공백 없는 3자 이상의 단독 복합명사만 허용, 예: 도로교통법, 주차장법)
_LAW_PATTERN_STANDALONE = re.compile(
    r"(?<![가-힣0-9])(?P<law>[가-힣]{3,20}(?:법률|법|시행령|시행규칙|규정|조례|규칙))(?![가-힣0-9])",
    re.UNICODE,
)


@dataclass
class LawReference:
    """인식된 법령 근거 정보"""

    raw_matched: str                  # 본문에서 매칭된 원문 (예: '개인정보 보호법 제15조')
    law_name: str                     # 추출된 법령명 (예: '개인정보 보호법')
    official_name: str = ""           # 국가법령정보센터 공식 법령명
    mst: str = ""                     # 법령 일련번호
    article_no: int = 0               # 조 번호 (예: 15)
    sub_article_no: int = 0           # 조 가지번호 (예: 제15조의2 -> 2)
    paragraph_no: int = 0             # 항 번호 (예: 제1항 -> 1)
    enforce_date: str = ""            # 인용 당시 시행일자 (YYYY.MM.DD)
    latest_enforce_date: str = ""     # 최신 시행일자
    latest_revision: str = ""         # 제개정구분 (일부개정, 전부개정 등)
    law_kind: str = ""                # 법령구분 (법률, 대통령령 등)
    office: str = ""                  # 소관부처
    is_revised: bool = False          # 개정 여부 (신규 시행일자 발견 시 True)
    article_title: str = ""           # 조문 제목 (예: '개인정보의 수집ㆍ이용')
    article_text: str = ""            # 최신 조문 원문

    @property
    def article_label(self) -> str:
        """'제15조', '제15조의2' 등의 조문 표기"""
        if not self.article_no:
            return ""
        if self.sub_article_no:
            res = f"제{self.article_no}조의{self.sub_article_no}"
        else:
            res = f"제{self.article_no}조"
        if self.paragraph_no:
            res += f"제{self.paragraph_no}항"
        return res

    @property
    def display_title(self) -> str:
        """표시용 타이틀 (예: 개인정보 보호법 제15조)"""
        lbl = self.article_label
        name = self.official_name or self.law_name
        return f"{name} {lbl}".strip()

    @property
    def url(self) -> str:
        if self.mst:
            ef = (self.latest_enforce_date or self.enforce_date).replace(".", "")
            if ef:
                return f"{LAW_DETAIL_BASE}?lsiSeq={self.mst}&efYd={ef}"
            return f"{LAW_DETAIL_BASE}?lsiSeq={self.mst}"
        return f"https://www.law.go.kr/unSc.do?target=law&query={self.law_name}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LawReference:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# 검색 결과 및 조문 메모리 캐시 (반복 API 호출 방지 및 즉시 완성본 제공)
_LAW_SEARCH_CACHE: dict[str, Any] = {}
_LAW_ARTICLE_CACHE: dict[tuple[str, int, int], dict[str, str]] = {}


def clean_law_candidate(name: str) -> str:
    """후보명에서 앞뒤 특수문자, 조사, 접속사 정리"""
    name = re.sub(r"^(대한민국|우리나라)\s+", "", name.strip())
    parts = name.split()
    while parts and (parts[0] in _CONJUNCTIONS or re.match(r"^(제?\d+조|제?\d+항)", parts[0])):
        parts.pop(0)
    return " ".join(parts).strip()


def is_valid_candidate(name: str) -> bool:
    """단어 자체가 법령명으로서 타당한 형태인지 사전 검증"""
    if not name or len(name) < 2:
        return False
    parts = name.split()
    last = parts[-1]
    if last in _STOP_WORDS:
        return False
    if any(last.endswith(sw) for sw in _STOP_SUFFIXES):
        return False
    return True


def is_valid_law_match(candidate: str, law_name: str) -> bool:
    """후보 검색어와 law.go.kr 결과 법령명이 실제로 동일/상응하는 법령인지 엄격 검증"""
    if not candidate or not law_name:
        return False
    c_norm = re.sub(r"[\s\·\ㆍ\(\)\-\_]", "", candidate)
    l_norm = re.sub(r"[\s\·\ㆍ\(\)\-\_]", "", law_name)
    if not c_norm or not l_norm:
        return False
    # 1. 완전 일치
    if c_norm == l_norm:
        return True
    # 2. candidate 가 공식 법령명에 포함되어 있고 최소 35% 이상 비중 차지
    if c_norm in l_norm:
        if len(c_norm) / len(l_norm) >= 0.35:
            return True
        core_l = re.sub(r"(에\s*관한|에\s*따른)?\s*(법률|시행령|시행규칙|규정|규칙|조례|법)$", "", l_norm)
        core_c = re.sub(r"(에\s*관한|에\s*따른)?\s*(법률|시행령|시행규칙|규정|규칙|조례|법)$", "", c_norm)
        if core_c and (core_c == core_l or core_c in core_l or core_l in core_c):
            return True
    elif l_norm in c_norm:
        if len(l_norm) / len(c_norm) >= 0.5:
            return True
    return False


def extract_laws_from_text(text: str, verify_online: bool = False) -> list[LawReference]:
    """텍스트 본문에서 법령 및 조문 언급을 정밀 추출합니다."""
    if not text or not text.strip():
        return []

    # HTML 태그 제거 및 unescape
    clean_text = re.sub(r"<[^>]+>", " ", text)
    clean_text = html.unescape(clean_text)

    found_map: dict[tuple[str, int, int], LawReference] = {}

    # 정밀도 순서대로 매칭 (1. 따옴표 명시 > 2. 조문 번호 수반 > 3. 고유 단독 명칭)
    patterns = [
        (_LAW_PATTERN_QUOTED, True),
        (_LAW_PATTERN_WITH_ARTICLE, True),
        (_LAW_PATTERN_STANDALONE, False),
    ]

    for pat, allow_multi_word in patterns:
        for m in pat.finditer(clean_text):
            raw_law = m.group("law").strip()
            law_candidate = clean_law_candidate(raw_law)
            if not is_valid_candidate(law_candidate):
                continue
            if not allow_multi_word and " " in law_candidate:
                continue

            art_no = int(m.groupdict().get("art") or 0)
            sub_art_no = int(m.groupdict().get("sub_art") or 0)
            hang_no = int(m.groupdict().get("hang") or 0)
            matched_str = m.group(0).strip()

            key = (law_candidate, art_no, sub_art_no)
            if key in found_map:
                continue

            # 만약 이미 같은 법령에 대해 구체적인 조문(art_no > 0)이 등록되어 있는데,
            # 현재 항목이 조문 없는 단순 법령명(art_no == 0)이면 중복 등록하지 않음
            if art_no == 0 and any(k[0] == law_candidate and k[1] > 0 for k in found_map):
                continue
            # 반대로 단순 법령명이 먼저 들어있었는데 구체적 조문이 발견되면 기존 단순 법령명 제거
            if art_no > 0 and (law_candidate, 0, 0) in found_map:
                del found_map[(law_candidate, 0, 0)]

            # 이미 오탐으로 확인된 단어는 스킵
            if _LAW_SEARCH_CACHE.get(law_candidate) is False:
                continue

            ref = LawReference(
                raw_matched=matched_str,
                law_name=law_candidate,
                article_no=art_no,
                sub_article_no=sub_art_no,
                paragraph_no=hang_no,
            )

            # 이미 조회된 법령이 캐시에 있으면 즉시 완성된 정보 주입
            cached = _LAW_SEARCH_CACHE.get(law_candidate)
            if cached and getattr(cached, "name", None):
                ref.official_name = cached.name
                ref.mst = cached.mst
                ref.law_kind = cached.law_kind
                ref.office = cached.office
                ref.latest_enforce_date = cached.enforce_date
                ref.latest_revision = cached.revision
                if not ref.enforce_date:
                    ref.enforce_date = cached.enforce_date
                if ref.mst and ref.article_no:
                    art_cached = _LAW_ARTICLE_CACHE.get((ref.mst, ref.article_no, ref.sub_article_no))
                    if art_cached:
                        ref.article_title = art_cached.get("title", "")
                        ref.article_text = art_cached.get("full_text", "")

            found_map[key] = ref

    results = list(found_map.values())
    # 같은 조문을 가리키는 더 긴 공식 법령명(예: '공유재산 및 물품 관리법')이 있으면
    # 부분 매칭된 짧은 이름(예: '물품 관리법')은 중복으로 제거한다.
    if len(results) > 1:
        results = [
            r for r in results
            if not any(
                other is not r
                and other.article_no == r.article_no
                and other.sub_article_no == r.sub_article_no
                and other.law_name != r.law_name
                and other.law_name.endswith(r.law_name)
                for other in results
            )
        ]
    if not results or not verify_online:
        return results

    return enrich_laws_online(results)


def enrich_laws_online(results: list[LawReference], timeout: int = 4) -> list[LawReference]:
    """백그라운드 스레드에서 국가법령정보센터 OpenAPI를 조회하여 법령 정보를 보강합니다."""
    if not results:
        return []

    verified_results: list[LawReference] = []
    for ref in results:
        cached = _LAW_SEARCH_CACHE.get(ref.law_name)
        if cached is None:
            try:
                search_res = search_laws(ref.law_name, limit=5, timeout=timeout, include_history=False)
                if not search_res:
                    search_res = search_laws(ref.law_name, limit=5, timeout=timeout, include_history=True)

                valid_law = None
                for sr in search_res:
                    if is_valid_law_match(ref.law_name, sr.name):
                        valid_law = sr
                        break

                if valid_law:
                    cached = valid_law
                    _LAW_SEARCH_CACHE[ref.law_name] = cached
                else:
                    _LAW_SEARCH_CACHE[ref.law_name] = False
            except Exception as e:
                logger.warning(f"Law search error for {ref.law_name}: {e}")
                _LAW_SEARCH_CACHE[ref.law_name] = False

        if cached and getattr(cached, "name", None):
            ref.official_name = cached.name
            ref.mst = cached.mst
            ref.law_kind = cached.law_kind
            ref.office = cached.office
            ref.latest_enforce_date = cached.enforce_date
            ref.latest_revision = cached.revision
            if not ref.enforce_date:
                ref.enforce_date = cached.enforce_date

            # 특정 조문이 지정된 경우 최신 조문 본문 가져오기
            if ref.mst and ref.article_no:
                art_key = (ref.mst, ref.article_no, ref.sub_article_no)
                art_info = _LAW_ARTICLE_CACHE.get(art_key)
                if art_info is None:
                    try:
                        art_info = get_law_article(ref.mst, ref.article_no, ref.sub_article_no)
                        if art_info:
                            _LAW_ARTICLE_CACHE[art_key] = art_info
                    except Exception as e:
                        logger.debug(f"Failed to fetch article text: {e}")
                if art_info:
                    ref.article_title = art_info.get("title", "")
                    ref.article_text = art_info.get("full_text", "")

            verified_results.append(ref)
        # 법령 검증 실패(실제 법령이 아님)한 항목은 verified_results에 포함하지 않음

    return verified_results


def check_for_revisions(saved_refs: list[LawReference]) -> list[LawReference]:
    """저장된 법령 근거 목록의 개정 여부를 확인합니다."""
    updated_refs: list[LawReference] = []
    for ref in saved_refs:
        if not ref.mst:
            updated_refs.append(ref)
            continue

        try:
            detail = get_law_detail(ref.mst)
            if detail:
                current_ef = detail.get("enforce_date", "")
                ref.latest_enforce_date = current_ef
                ref.latest_revision = detail.get("revision_type", "")
                if ref.enforce_date and current_ef and ref.enforce_date != current_ef:
                    # 저장된 시행일자와 현재 최신 시행일자가 다르면 개정됨!
                    ref.is_revised = True

                # 최신 조문 본문 갱신
                if ref.article_no and "articles" in detail:
                    art_info = detail["articles"].get((ref.article_no, ref.sub_article_no))
                    if art_info:
                        ref.article_title = art_info.get("title", "")
                        ref.article_text = art_info.get("full_text", "")
        except Exception as e:
            logger.warning(f"Error checking revision for {ref.display_title}: {e}")

        updated_refs.append(ref)
    return updated_refs


def compare_snippets(user_snippet: str, official_text: str) -> dict[str, Any]:
    """사용자 작성 내용과 공식 법령 조문 텍스트 간 차이점 분석"""
    s1 = user_snippet.strip()
    s2 = official_text.strip()

    # 줄 단위 Diff 생성
    diff_lines = list(difflib.unified_diff(
        s1.splitlines(),
        s2.splitlines(),
        fromfile="문서 작성 내용",
        tofile="최신 법령 조문",
        lineterm="",
    ))

    # 유사도 비율 (0.0 ~ 1.0)
    matcher = difflib.SequenceMatcher(None, s1, s2)
    similarity = matcher.ratio()

    is_identical = (similarity >= 0.98)

    return {
        "similarity": round(similarity * 100, 1),
        "is_identical": is_identical,
        "diff_text": "\n".join(diff_lines),
        "user_text": s1,
        "law_text": s2,
    }


def html_unescape(text: str) -> str:
    """HTML 엔티티 변환"""
    import html
    return html.unescape(text)
