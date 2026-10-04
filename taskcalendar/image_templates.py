"""
공문서 및 행정업무용 표준 이미지 템플릿 생성기 및 라이브러리
- 직인/관인(Official Seal)
- 결재인/결재선(Approval Stamp)
- 원본대조필, 대외비, 긴급, 사본, 접수 도장
- 행정 서식 불릿/항목 기호 (□, ○, -, ※, ★, 끝.)
- 업무 통계 차트 (막대/원형/추이 그래프)
- 업무 프로세스 플로우차트 / 다이어그램
"""
import os
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def get_visual_templates() -> list[dict]:
    """일러스트, 장식, 이미지·카툰 등 '이미지 템플릿' 폴더의 서브디렉토리를 스캔하여 이미지 라이브러리 목록 반환"""
    from taskcalendar.paths import runtime_root
    storage_dir = runtime_root() / "이미지 템플릿"
    if not storage_dir.exists():
        storage_dir.mkdir(parents=True, exist_ok=True)

    # 1) 메타데이터 파일 확인 (한글 타이틀 매핑용)
    meta_map = {}
    meta_candidates = [
        storage_dir / "templates_meta.json",
        Path(__file__).resolve().parent.parent / "assets" / "img_templates" / "all_templates.json"
    ]
    for mf in meta_candidates:
        if mf.exists():
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    for it in json.load(f):
                        t = it.get("title")
                        if t:
                            if it.get("png_filename"):
                                meta_map[it["png_filename"]] = t
                            if it.get("filename"):
                                meta_map[it["filename"]] = t
            except Exception:
                pass
            break

    # 2) 서브디렉토리 순회 및 이미지 파일 수집
    supported_exts = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico"}
    results = []

    # 기본 우선순위 카테고리 순서
    known_order = ["일러스트", "장식", "이미지·카툰"]
    existing_dirs = [p for p in storage_dir.iterdir() if p.is_dir()]
    # known_order 먼저, 그 외 사용자가 추가한 폴더는 가나다 순으로 정렬
    sorted_dirs = sorted(existing_dirs, key=lambda p: (0, known_order.index(p.name)) if p.name in known_order else (1, p.name))

    for cat_dir in sorted_dirs:
        cat_name = cat_dir.name
        # 해당 카테고리 폴더 안의 이미지 파일들 스캔
        for file_path in sorted(cat_dir.iterdir(), key=lambda p: p.name.lower()):
            if file_path.is_file() and file_path.suffix.lower() in supported_exts:
                fname = file_path.name
                # 타이틀: 메타데이터에 등록된 제목이 있으면 사용, 없으면 파일명(확장자 제외)
                title = meta_map.get(fname, file_path.stem)
                results.append({
                    "id": f"{cat_name}_{fname}",
                    "category": cat_name,
                    "title": title,
                    "filename": fname,
                    "format": file_path.suffix.lower().lstrip("."),
                    "path": str(file_path),
                    "desc": ""
                })

    return results

def ensure_font(size: int, bold: bool = False):
    font_paths = [
        'C:/Windows/Fonts/malgunbd.ttf' if bold else 'C:/Windows/Fonts/malgun.ttf',
        'C:/Windows/Fonts/malgun.ttf',
        'C:/Windows/Fonts/batang.ttc',
        'C:/Windows/Fonts/gulim.ttc',
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()

def create_official_seal(org_name: str = "행정기관", save_path: str = "") -> str:
    """공인/직인 (규격 정방형 관인 200x200)"""
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = "#B91C1C"

    # 외곽 테두리 (깔끔한 단일 테두리)
    draw.rounded_rectangle([8, 8, 192, 192], radius=10, outline=color, width=4)

    chars = list(org_name[:4])
    while len(chars) < 4:
        chars.append("인")

    font = ensure_font(34, bold=True)
    # 우측 상단 -> 우측 하단 -> 좌측 상단 -> 좌측 하단 (전통 관인 배치) 또는 현대식 가로/세로 배치
    # 2x2 그리드
    draw.text((36, 36), chars[0], font=font, fill=color)
    draw.text((36, 106), chars[1], font=font, fill=color)
    draw.text((110, 36), chars[2], font=font, fill=color)
    draw.text((110, 106), chars[3], font=font, fill=color)

    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img.save(save_path, "PNG")
    return save_path

def create_approval_stamp(roles: list[str] = None, save_path: str = "") -> str:
    """결재인 (4칸 기본: 결재 / 담당 / 팀장 / 과장)"""
    if not roles:
        roles = ["담당", "팀장", "과장", "국장"]

    width = 80 + len(roles) * 75
    height = 96
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    border_color = "#1E293B"

    # 외곽선
    draw.rectangle([2, 2, width - 2, height - 2], outline=border_color, width=2)
    # 좌측 '결재' 헤더 칸
    header_w = 42
    draw.rectangle([2, 2, header_w, height - 2], fill="#F8FAFC", outline=border_color, width=1)
    
    font_header = ensure_font(13, bold=True)
    font_role = ensure_font(12, bold=True)
    font_date = ensure_font(10, bold=False)

    draw.text((15, 24), "결", font=font_header, fill=border_color)
    draw.text((15, 52), "재", font=font_header, fill=border_color)

    # 직급 헤더 높이
    title_h = 28
    draw.line([header_w, title_h, width - 2, title_h], fill=border_color, width=1)

    col_w = (width - 2 - header_w) // len(roles)
    for i, role in enumerate(roles):
        x_left = header_w + i * col_w
        if i > 0:
            draw.line([x_left, 2, x_left, height - 2], fill=border_color, width=1)
        
        # 직급 텍스트 중앙 정렬
        draw.text((x_left + (col_w - len(role) * 12) // 2, 7), role, font=font_role, fill=border_color)

    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img.save(save_path, "PNG")
    return save_path

def create_badge_stamp(text: str, color: str = "#DC2626", bg: str = "#FEF2F2", sub_text: str = "", save_path: str = "") -> str:
    """행정 고무인 / 상태 도장 (원본대조필, 대외비, 긴급, 접수 등)"""
    width = 170
    height = 68 if sub_text else 46
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([2, 2, width - 2, height - 2], radius=6, outline=color, fill=bg, width=2)

    font_main = ensure_font(18, bold=True)
    # 텍스트 중앙 배치
    text_spaced = " ".join(list(text)) if len(text) <= 4 else text
    draw.text((18, 11), text_spaced, font=font_main, fill=color)

    if sub_text:
        draw.line([2, 42, width - 2, 42], fill=color, width=1)
        font_sub = ensure_font(11, bold=False)
        draw.text((10, 48), sub_text, font=font_sub, fill=color)

    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img.save(save_path, "PNG")
    return save_path

def create_end_mark(save_path: str = "") -> str:
    """공문서 표준 종결 부호 '끝.' 배지 이미지"""
    img = Image.new("RGBA", (90, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, 88, 30], radius=4, outline="#2563EB", fill="#EFF6FF", width=1)
    font = ensure_font(16, bold=True)
    draw.text((26, 5), "끝.", font=font, fill="#1D4ED8")
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img.save(save_path, "PNG")
    return save_path

def create_process_flow_chart(steps: list[str], title: str = "업무 추진 절차", save_path: str = "") -> str:
    """업무 흐름도 / 프로세스 다이어그램 생성"""
    step_count = len(steps)
    box_w = 140
    box_h = 56
    gap = 40
    pad_x = 30
    pad_y = 50
    width = pad_x * 2 + step_count * box_w + (step_count - 1) * gap
    height = pad_y + box_h + 30

    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    font_title = ensure_font(15, bold=True)
    font_step_no = ensure_font(11, bold=True)
    font_step_name = ensure_font(12, bold=True)

    draw.text((pad_x, 15), f"□ {title}", font=font_title, fill="#0F172A")

    colors = ["#2563EB", "#0D9488", "#7C3AED", "#D97706", "#DC2626"]

    for i, step in enumerate(steps):
        x = pad_x + i * (box_w + gap)
        y = pad_y
        c = colors[i % len(colors)]

        # 박스
        draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=8, outline=c, fill="#F8FAFC", width=2)
        # 상단 번호 바
        draw.rounded_rectangle([x + 2, y + 2, x + box_w - 2, y + 20], radius=4, fill=c)
        draw.text((x + 10, y + 4), f"STEP {i+1:02d}", font=font_step_no, fill="#FFFFFF")
        # 스텝 명칭
        draw.text((x + 12, y + 28), step[:12], font=font_step_name, fill="#1E293B")

        # 화살표 (마지막 스텝 제외)
        if i < step_count - 1:
            arrow_start_x = x + box_w + 6
            arrow_end_x = arrow_start_x + gap - 12
            arrow_y = y + box_h // 2
            draw.line([arrow_start_x, arrow_y, arrow_end_x, arrow_y], fill="#94A3B8", width=3)
            # 화살표 머리
            draw.polygon([
                (arrow_end_x, arrow_y - 6),
                (arrow_end_x + 8, arrow_y),
                (arrow_end_x, arrow_y + 6)
            ], fill="#94A3B8")

    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img.save(save_path, "PNG")
    return save_path

def generate_default_templates(target_dir: Path) -> list[dict]:
    """표준 행정 이미지 템플릿 기본 세트 일괄 생성 및 메타데이터 반환"""
    target_dir.mkdir(parents=True, exist_ok=True)

    items = [
        {
            "id": "seal_gov",
            "category": "직인·관인",
            "title": "행정기관 직인",
            "desc": "",
            "func": lambda p: create_official_seal("행정기관", p),
            "filename": "seal_official.png"
        },
        {
            "id": "seal_dept",
            "category": "직인·관인",
            "title": "부서 전용 직인",
            "desc": "",
            "func": lambda p: create_official_seal("업무전용", p),
            "filename": "seal_dept.png"
        },
        {
            "id": "approval_4",
            "category": "결재선",
            "title": "4단계 결재인",
            "desc": "",
            "func": lambda p: create_approval_stamp(["담당", "팀장", "과장", "국장"], p),
            "filename": "approval_4steps.png"
        },
        {
            "id": "approval_3",
            "category": "결재선",
            "title": "3단계 결재인",
            "desc": "",
            "func": lambda p: create_approval_stamp(["담당", "팀장", "부서장"], p),
            "filename": "approval_3steps.png"
        },
        {
            "id": "badge_original",
            "category": "고무인·상태인",
            "title": "원본대조필",
            "desc": "",
            "func": lambda p: create_badge_stamp("원본대조필", color="#DC2626", bg="#FEF2F2", sub_text="직급:             성명:         (인)", save_path=p),
            "filename": "badge_original_verified.png"
        },
        {
            "id": "badge_confidential",
            "category": "고무인·상태인",
            "title": "대외비",
            "desc": "",
            "func": lambda p: create_badge_stamp("대외비", color="#EA580C", bg="#FFF7ED", save_path=p),
            "filename": "badge_confidential.png"
        },
        {
            "id": "badge_urgent",
            "category": "고무인·상태인",
            "title": "긴급 처리",
            "desc": "",
            "func": lambda p: create_badge_stamp("긴급", color="#E11D48", bg="#FFF1F2", save_path=p),
            "filename": "badge_urgent.png"
        },
        {
            "id": "badge_received",
            "category": "고무인·상태인",
            "title": "접수인",
            "desc": "",
            "func": lambda p: create_badge_stamp("접수인", color="#2563EB", bg="#EFF6FF", sub_text="접수일자: 2026.    .    .", save_path=p),
            "filename": "badge_received.png"
        },
        {
            "id": "mark_end",
            "category": "서식기호",
            "title": "공문서 종결 부호 (끝.)",
            "desc": "",
            "func": lambda p: create_end_mark(p),
            "filename": "mark_end.png"
        },
        {
            "id": "flow_general",
            "category": "프로세스",
            "title": "업무 추진 절차도",
            "desc": "",
            "func": lambda p: create_process_flow_chart(["계획 수립", "검토 및 협의", "사업 집행", "결과 보고"], "업무 추진 절차", p),
            "filename": "flow_4steps.png"
        },
        {
            "id": "flow_contract",
            "category": "프로세스",
            "title": "계약 및 구매 절차도",
            "desc": "",
            "func": lambda p: create_process_flow_chart(["구매 요구", "일상 감사", "계약 체결", "검수 및 정산"], "계약·지출 절차", p),
            "filename": "flow_contract.png"
        }
    ]

    for item in items:
        file_path = str(target_dir / item["filename"])
        item["path"] = file_path
        if not os.path.exists(file_path):
            try:
                item["func"](file_path)
            except Exception as e:
                pass
    return items
