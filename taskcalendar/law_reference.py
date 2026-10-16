"""국가법령정보센터(law.go.kr) 법령 검색 지원.

공식 OpenAPI(DRF)를 사용한다.
- 현행법령(target=law)과 연혁법령(target=eflaw)을 모두 조회해야
  '개인정보 보호에 관한 법률' 처럼 개칭이 바뀐 구법령도 잡힌다.
- 응답의 <현행연혁코드> 로 현행/연혁을 정확히 구분할 수 있다.
- 법령명 인덱싱이 축약형(핵심어+법령종류)을 선호하므로 검색어를 변형해 재시도한다.
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LAW_SEARCH_API = "https://www.law.go.kr/DRF/lawSearch.do"
LAW_SERVICE_API = "https://www.law.go.kr/DRF/lawService.do"
LAW_DETAIL_BASE = "https://www.law.go.kr/lsInfoP.do"
LAW_SEARCH_PAGE = "https://www.law.go.kr/unSc.do?target=law&query="
LAW_HOME = "https://www.law.go.kr"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# OpenAPI 인증키(OC)
_OC = "westock"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_LAW_BLOCK_RE = re.compile(r"<law\b[^>]*>(.*?)</law>", re.S)
_TAG_RE = re.compile(r"<([^>]+)>(.*?)</\1>", re.S)

_LAW_KIND_WORDS = ("시행규칙", "시행령", "법률", "규칙", "령")
_KIND_RANK = {"법률": 0, "대통령령": 1, "총리령": 1, "부령": 2, "규칙": 3}


def set_oc(oc: str) -> None:
    """기관에서 발급받은 OpenAPI 키를 사용하도록 전환한다."""
    global _OC
    oc = (oc or "").strip()
    if oc:
        _OC = oc


def current_oc() -> str:
    return _OC


def _clean(value: str) -> str:
    value = _CDATA_RE.sub(r"\1", value or "")
    return html.unescape(value).strip()


def _fmt_date(raw: str) -> str:
    raw = re.sub(r"[^0-9]", "", raw or "")
    if len(raw) >= 8:
        return f"{raw[:4]}.{raw[4:6]}.{raw[6:8]}"
    return ""


def _enforce_num(value: str) -> int:
    return int(re.sub(r"\D", "", value or "") or 0)


class LawSearchResult:
    """검색된 법령 1건"""

    __slots__ = ("mst", "name", "law_kind", "promulgate_date", "enforce_date",
                 "revision", "is_current", "office")

    def __init__(self, mst: str, name: str, law_kind: str = "", office: str = "",
                 promulgate_date: str = "", enforce_date: str = "",
                 revision: str = "", is_current: bool = False) -> None:
        self.mst = mst
        self.name = name
        self.law_kind = law_kind
        self.office = office
        self.promulgate_date = promulgate_date
        self.enforce_date = enforce_date
        self.revision = revision
        self.is_current = is_current

    @property
    def url(self) -> str:
        """국가법령정보센터 상세 페이지 URL (브라우저로 열면 된다)."""
        ef = (self.enforce_date or "").replace(".", "")
        if ef:
            return f"{LAW_DETAIL_BASE}?lsiSeq={self.mst}&efYd={ef}"
        return f"{LAW_DETAIL_BASE}?lsiSeq={self.mst}"

    def status_text(self) -> str:
        return "현행" if self.is_current else "연혁"

    def display(self) -> str:
        bits = [self.name]
        meta: list[str] = []
        if self.law_kind:
            meta.append(self.law_kind)
        if self.enforce_date:
            meta.append(f"시행 {self.enforce_date}")
        if meta:
            bits.append(f"[{' · '.join(meta)}]")
        return " ".join(bits)

    def detail_text(self) -> str:
        bits = []
        if self.office:
            bits.append(f"소관: {self.office}")
        if self.promulgate_date:
            bits.append(f"공포 {self.promulgate_date}")
        if self.revision:
            bits.append(self.revision)
        if not self.is_current:
            bits.append("※ 연혁법령")
        return "  ·  ".join(bits)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Law {self.name} mst={self.mst} current={self.is_current}>"


def _extract_query_terms(query: str) -> list[str]:
    """검색어를 여러 형태로 확장한다.

    law.go.kr DRF 의 법령명 인덱싱은 '개인정보 보호법' 처럼
    핵심어 + 법령종류 의 축약형을 우선 매칭한다.
    정식 명칭(예: '개인정보 보호에 관한 법률') 은 현행법령에서 0건이 될 수 있어,
    검색 성향을 높이기 위해 형태를 변형해 순차 시도한다.
    """
    q = (query or "").strip()
    if not q:
        return []

    terms: list[str] = [q]

    # 'A에 관한 법률' → 'A'  (과잉 수식어 제거)
    stripped = re.sub(r"\s*(에\s*관한|에\s*따른)\s*(법률|시행령|시행규칙|규칙)$",
                      "", q).strip()
    if not stripped or stripped == q:
        # '공공기관의 개인정보 보호' 처럼 중간 조사가 붙은 경우
        stripped = re.sub(r"의(?=\s)", " ", q)
        stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped and stripped != q:
        terms.append(stripped)

    # 'A법' → 'A' (법령종류 접미사 제거)
    for kind in _LAW_KIND_WORDS:
        if stripped.endswith(kind) and len(stripped) > len(kind):
            base = stripped[: -len(kind)].strip()
            if base:
                terms.append(base)
                core = base.replace(" ", "")
                if len(core) >= 2 and core != base:
                    terms.append(core)
            break

    # 공백 단위 핵심어 (2자 이상)
    for w in re.split(r"\s+", stripped):
        w = w.strip()
        if len(w) >= 2:
            terms.append(w)

    # 공백 제거 형태
    joined = stripped.replace(" ", "")
    if len(joined) >= 2:
        terms.append(joined)

    # 순서 유지 중복 제거
    return list(dict.fromkeys(terms))


def _fetch(target: str, term: str, timeout: int) -> list[LawSearchResult]:
    """DRF 법령검색 한 건을 호출해 결과 리스트로 변환한다."""
    try:
        resp = requests.get(
            LAW_SEARCH_API,
            params={
                "OC": _OC,
                "target": target,
                "query": term,
                "type": "XML",
                "display": "30",
            },
            headers=_HEADERS,
            timeout=timeout,
        )
    except Exception:
        return []

    if resp.status_code != 200:
        return []

    # 태그를 먼저 벗기면 CDATA 안의 값까지 사라지므로,
    # 블록/태그 파싱을 먼저 하고 각 필드값에만 _clean 을 적용한다.
    text = resp.content.decode("utf-8", errors="replace")

    out: list[LawSearchResult] = []
    for block in _LAW_BLOCK_RE.findall(text):
        fields = {k: _clean(v) for k, v in _TAG_RE.findall(block)}
        mst = fields.get("법령일련번호", "").strip()
        name = fields.get("법령명한글", "").strip()
        if not mst or not name:
            continue
        out.append(LawSearchResult(
            mst=mst,
            name=name,
            law_kind=fields.get("법령구분명", ""),
            office=fields.get("소관부처명", ""),
            promulgate_date=_fmt_date(fields.get("공포일자", "")),
            enforce_date=_fmt_date(fields.get("시행일자", "")),
            revision=fields.get("제개정구분명", ""),
            is_current=fields.get("현행연혁코드", "").startswith("현행"),
        ))
    return out


def search_laws(query: str, limit: int = 20, timeout: int = 20,
                include_history: bool = True) -> list[LawSearchResult]:
    """law.go.kr 공식 API 로 법령을 검색한다.

    현행법령과 연혁법령을 모두 조회하고, 검색어와 일치하는 법령을 앞에 배치한다.
    include_history=False 이면 현행 법령만 반환한다.
    """
    query = (query or "").strip()
    if not query:
        return []

    collected: dict[str, LawSearchResult] = {}

    # 1단계: 현행법령(law) 우선 조회 (최신 현행법령 즉시 확보)
    terms = _extract_query_terms(query)
    for term in terms:
        for r in _fetch("law", term, timeout):
            if r.mst not in collected:
                collected[r.mst] = r

    # 2단계: 연혁법령(eflaw) 보완 조회 (개칭 전 구법령 또는 시행예정 법령 보완)
    if include_history:
        for term in terms:
            for r in _fetch("eflaw", term, timeout):
                if r.mst not in collected:
                    collected[r.mst] = r

    results = list(collected.values())
    if not results:
        return []

    q_norm = re.sub(r"[\s\·\ㆍ\(\)\-\_]", "", query)

    def match_quality(r: LawSearchResult) -> int:
        if not q_norm:
            return 9
        name_norm = re.sub(r"[\s\·\ㆍ\(\)\-\_]", "", r.name)
        if q_norm == name_norm:
            return 0 if r.is_current else 2  # 완전 일치
        if q_norm in name_norm:
            ratio = len(q_norm) / len(name_norm)
            if ratio >= 0.35:
                return 1 if r.is_current else 3  # 검색어가 법령명의 유의미한 비중 차지
        if name_norm in q_norm:
            return 1 if r.is_current else 3

        # 핵심어 검사 ('...에 관한 법률' 등)
        core_q = re.sub(r"(에관한|에따른)?(법률|시행령|시행규칙|규정|규칙|조례|법)$", "", q_norm)
        core_n = re.sub(r"(에관한|에따른)?(법률|시행령|시행규칙|규정|규칙|조례|법)$", "", name_norm)
        if core_q and core_n and (core_q == core_n or core_q in core_n or core_n in core_q):
            return 1 if r.is_current else 3

        # 단순 부분 포함이나 무관한 법령은 최하위 등급
        return 8 if r.is_current else 9

    def sort_key(r: LawSearchResult) -> tuple:
        return (
            match_quality(r),
            _KIND_RANK.get(r.law_kind, 9),
            -_enforce_num(r.enforce_date),
        )

    results.sort(key=sort_key)
    return results[:limit]


def search_current_laws(query: str, limit: int = 20) -> list[LawSearchResult]:
    """현행 법령만 반환."""
    return [r for r in search_laws(query, limit=limit * 2) if r.is_current][:limit]


# 호환성 별칭
search_law = search_laws


def build_search_page_url(query: str) -> str:
    """law.go.kr 통합검색 페이지 URL (검색 실패 시 폴백)."""
    return f"{LAW_SEARCH_PAGE}{quote(query)}"


def build_home_url() -> str:
    return LAW_HOME


def get_law_detail(mst: str, target: str = "law", timeout: int = 15) -> dict | None:
    """국가법령정보센터 DRF lawService.do 로 법령 상세 본문(조문 목록 등)을 조회한다."""
    try:
        resp = requests.get(
            LAW_SERVICE_API,
            params={
                "OC": _OC,
                "target": target,
                "MST": str(mst).strip(),
                "type": "XML",
            },
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None

        import defusedxml.ElementTree as ET

        root = ET.fromstring(resp.content)
        law_name = root.findtext(".//기본정보/법령명_한글") or root.findtext(".//법령명_한글") or ""
        enforce_date = _fmt_date(root.findtext(".//시행일자") or "")
        promulgate_date = _fmt_date(root.findtext(".//공포일자") or "")
        revision_type = root.findtext(".//제개정구분명") or ""
        office = root.findtext(".//소관부처명") or ""

        articles: dict[tuple[int, int], dict] = {}
        for art in root.findall(".//조문단위"):
            no_raw = art.findtext("조문번호") or ""
            sub_no_raw = art.findtext("조문가지번호") or "0"
            try:
                art_no = int(re.sub(r"\D", "", no_raw) or "0")
                sub_no = int(re.sub(r"\D", "", sub_no_raw) or "0")
            except Exception:
                continue

            title = (art.findtext("조문제목") or "").strip()
            content = (art.findtext("조문내용") or "").strip()

            hangs = []
            for hang in art.findall(".//항"):
                h_no = (hang.findtext("항번호") or "").strip()
                h_content = (hang.findtext("항내용") or "").strip()
                hos = []
                for ho in hang.findall(".//호"):
                    ho_no = (ho.findtext("호번호") or "").strip()
                    ho_content = (ho.findtext("호내용") or "").strip()
                    hos.append(f"  {ho_no} {ho_content}".strip())
                hang_text = f"{h_no} {h_content}".strip()
                if hos:
                    hang_text += "\n" + "\n".join(hos)
                if hang_text:
                    hangs.append(hang_text)

            full_text = content
            if hangs:
                full_text = content + "\n" + "\n".join(hangs)

            key = (art_no, sub_no)
            articles[key] = {
                "article_no": art_no,
                "sub_article_no": sub_no,
                "title": title,
                "content": content,
                "full_text": full_text.strip(),
            }

        return {
            "mst": str(mst).strip(),
            "name": law_name.strip(),
            "enforce_date": enforce_date,
            "promulgate_date": promulgate_date,
            "revision_type": revision_type,
            "office": office,
            "articles": articles,
        }
    except Exception:
        return None


def get_law_article(mst: str, article_no: int, sub_article_no: int = 0) -> dict | None:
    """특정 조문(예: 제15조 또는 제15조의2)의 원문 데이터를 조회한다."""
    detail = get_law_detail(mst)
    if not detail or "articles" not in detail:
        return None
    return detail["articles"].get((article_no, sub_article_no))