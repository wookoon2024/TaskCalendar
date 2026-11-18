"""바로업무 스티커 v2 생성기.

각 스티커를 SVG 로 그리고 Qt(QSvgRenderer)로 PNG 를 만든다.
- 정지 스티커: 128×128 PNG 한 장
- 움직이는 스티커: 128×128 프레임 8장을 가로로 이은 시트(1024×128)

사용:
    python tools/sticker_v2_gen.py <출력폴더> [--only 이름,이름]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SIZE = 128
FRAMES = 8

# ── 공통 색 (파스텔 + 얇은 진회색 외곽선) ──────────────────────────────
INK = "#3F3A36"          # 외곽선
STROKE = 3
FACE = "#FFD978"         # 얼굴 기본
FACE_ANGRY = "#FF9C7A"
FACE_GLOOM = "#BFD3EE"
CHEEK = "#FF9EAA"
GREEN = "#2BB38A"
GREEN_DARK = "#1F7A67"
RED = "#F26464"
BLUE = "#6FA8E8"
CREAM = "#FFF6E3"
WOOD = "#C98A55"


def svg(body: str, w: int = SIZE, h: int = SIZE) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>'
    )


def phase(i: int) -> float:
    """0..1 사이 진행도"""
    return i / FRAMES


def wave(i: int, amp: float = 1.0) -> float:
    return math.sin(phase(i) * 2 * math.pi) * amp


# ── 개별 스티커 ─────────────────────────────────────────────────────────

def deadline_flag(i: int) -> str:
    """마감 깃발: 깃발이 바람에 펄럭인다."""
    w1, w2 = wave(i, 6), wave(i + 2, 6)
    flag = (
        f"M44 26 C62 {18 + w1:.1f} 78 {34 + w2:.1f} 100 {26 + w1:.1f} "
        f"L100 {62 + w2:.1f} C78 {70 + w1:.1f} 62 {54 + w2:.1f} 44 {62 + w1:.1f} Z"
    )
    return svg(
        f'<ellipse cx="42" cy="114" rx="22" ry="5" fill="#E9E1D3"/>'
        f'<path d="{flag}" fill="{RED}" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M56 36 L56 48 M64 40 L64 52" stroke="#FFFFFF" stroke-width="3" opacity="0.6"/>'
        f'<rect x="36" y="18" width="8" height="96" rx="4" fill="{WOOD}" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<circle cx="40" cy="16" r="6" fill="#FFD25E" stroke="{INK}" stroke-width="{STROKE}"/>'
    )


def _mug(x: float, angle: float, flip: bool) -> str:
    sx = -1 if flip else 1
    return (
        f'<g transform="translate({x} 64) rotate({angle}) scale({sx} 1)">'
        f'<rect x="-20" y="-26" width="34" height="50" rx="6" fill="#FFC94D" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M14 -14 h8 a8 8 0 0 1 8 8 v8 a8 8 0 0 1 -8 8 h-8" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M-22 -26 c4 -10 12 -10 16 -4 c4 -8 14 -8 18 0 c4 -4 10 -2 10 4 z" fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M-10 -12 v26 M0 -12 v26" stroke="#FFFFFF" stroke-width="3" opacity="0.7"/>'
        f'</g>'
    )


def cheers(i: int) -> str:
    """회식: 맥주잔 두 개가 부딪히고 '쨍' 반짝임."""
    t = phase(i)
    hit = math.sin(t * math.pi)  # 0 → 1(부딪힘, 4번째 프레임) → 0
    tilt = 4 + 14 * hit          # 부딪힐 때 안쪽으로 더 기울어짐
    gap = 2 + 24 * (1 - hit)     # 멀리서 다가와 부딪힘
    sparks = ""
    if i in (3, 4, 5):
        alpha = {3: 0.5, 4: 1.0, 5: 0.6}[i]
        sparks = (
            f'<g stroke="#FF8A3D" stroke-width="4" opacity="{alpha}">'
            f'<path d="M64 18 v-10"/><path d="M50 22 l-6 -8"/><path d="M78 22 l6 -8"/></g>'
        )
    return svg(
        f'<ellipse cx="64" cy="112" rx="40" ry="5" fill="#E9E1D3"/>'
        + _mug(64 - 20 - gap / 2, tilt, True)    # 왼쪽 잔: 손잡이 바깥(왼쪽)
        + _mug(64 + 20 + gap / 2, -tilt, False)  # 오른쪽 잔: 손잡이 바깥(오른쪽)
        + sparks
    )


def promotion(i: int) -> str:
    """승진: 계단 위로 오르는 화살표와 반짝이는 별."""
    tw = (math.sin(phase(i) * 2 * math.pi) + 1) / 2
    star = lambda cx, cy, r, a: (  # noqa: E731
        f'<path d="M{cx} {cy - r} L{cx + r * 0.28} {cy - r * 0.28} L{cx + r} {cy} L{cx + r * 0.28} {cy + r * 0.28} '
        f'L{cx} {cy + r} L{cx - r * 0.28} {cy + r * 0.28} L{cx - r} {cy} L{cx - r * 0.28} {cy - r * 0.28} Z" '
        f'fill="#FFD25E" stroke="{INK}" stroke-width="2" opacity="{a:.2f}"/>'
    )
    return svg(
        f'<path d="M16 108 h28 v-22 h28 v-22 h28 v-22 h12 v66 z" fill="#BFE6D6" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M24 92 L88 30" stroke="{GREEN_DARK}" stroke-width="9"/>'
        f'<path d="M70 26 L94 24 L92 48" stroke="{GREEN_DARK}" stroke-width="9"/>'
        + star(32, 40, 12 + 3 * tw, 0.4 + 0.6 * tw)
        + star(104, 92, 8 + 2 * (1 - tw), 0.4 + 0.6 * (1 - tw))
    )


def tea_time(i: int) -> str:
    """차 마시기: 찻잔에서 김이 모락모락."""
    t = phase(i)
    steam = ""
    for k, x in enumerate((50, 64, 78)):
        p = (t + k / 3) % 1.0
        y = 46 - p * 26
        a = math.sin(p * math.pi)
        steam += (
            f'<path d="M{x} {y + 14:.1f} c-6 -5 6 -9 0 -14" stroke="#B9AFA4" stroke-width="4" opacity="{a:.2f}"/>'
        )
    return svg(
        f'<ellipse cx="64" cy="104" rx="46" ry="9" fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M28 58 h68 v10 a34 30 0 0 1 -68 0 z" fill="#9ED8C6" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M96 66 a12 12 0 0 1 0 22 h-6" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<ellipse cx="62" cy="58" rx="34" ry="6" fill="#C98A55" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M50 74 q12 8 24 0" stroke="#FFFFFF" stroke-width="3" opacity="0.6"/>'
        + steam
    )


def meal(_i: int = 0) -> str:
    """식사: 밥그릇과 수저."""
    return svg(
        f'<ellipse cx="60" cy="110" rx="40" ry="6" fill="#E9E1D3"/>'
        f'<path d="M22 62 h76 a38 40 0 0 1 -76 0 z" fill="#7FB9E6" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M34 74 q26 12 52 0" stroke="#FFFFFF" stroke-width="3" opacity="0.6"/>'
        f'<path d="M24 62 c4 -22 66 -22 72 0 z" fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<circle cx="46" cy="52" r="2" fill="{INK}"/><circle cx="60" cy="48" r="2" fill="{INK}" opacity="0.5"/>'
        f'<circle cx="74" cy="54" r="2" fill="{INK}" opacity="0.5"/>'
        f'<path d="M98 20 L84 58 M108 24 L92 60" stroke="{WOOD}" stroke-width="5"/>'
        f'<path d="M98 20 L84 58 M108 24 L92 60" stroke="{INK}" stroke-width="1.5" opacity="0.5"/>'
    )


def done_check(i: int) -> str:
    """완료 체크: 초록 원 안에 체크가 그려진다 (마지막 프레임에서 완성)."""
    prog = min(1.0, (i + 1) / (FRAMES - 2))
    length = 70
    dash = f'stroke-dasharray="{length * prog:.1f} {length}"'
    pop = 1.0 + (0.06 if i == FRAMES - 2 else 0.0)
    return svg(
        f'<g transform="translate(64 64) scale({pop}) translate(-64 -64)">'
        f'<circle cx="64" cy="64" r="46" fill="#BFE6D6" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<circle cx="64" cy="64" r="36" fill="{GREEN}"/>'
        f'<path d="M44 66 L58 80 L86 50" stroke="#FFFFFF" stroke-width="11" {dash}/>'
        f'</g>'
    )


def _face(fill: str, extra: str, cy: int = 70) -> str:
    """동그란 얼굴 틀 + 표정. cy 를 바꾸면 얼굴 전체(표정 포함)를 위아래로 옮긴다."""
    body = f'<circle cx="64" cy="70" r="42" fill="{fill}" stroke="{INK}" stroke-width="{STROKE}"/>' + extra
    return body if cy == 70 else f'<g transform="translate(0 {cy - 70})">{body}</g>'


def angry(i: int) -> str:
    """화남: 빨간 얼굴, 머리 위로 김이 푹푹."""
    t = phase(i)
    puffs = ""
    for k, x in enumerate((40, 88)):
        p = (t + k * 0.5) % 1.0
        r = 6 + p * 8
        a = 1 - p
        puffs += f'<circle cx="{x}" cy="{30 - p * 14:.1f}" r="{r:.1f}" fill="#FFFFFF" stroke="#C9C0B5" stroke-width="2" opacity="{a:.2f}"/>'
    shake = wave(i * 2, 1.5)
    face = _face(
        FACE_ANGRY,
        f'<path d="M40 58 L56 64 M88 58 L72 64" stroke="{INK}" stroke-width="5"/>'
        f'<circle cx="50" cy="72" r="4" fill="{INK}"/><circle cx="78" cy="72" r="4" fill="{INK}"/>'
        f'<path d="M50 94 q14 -10 28 0" stroke="{INK}" stroke-width="4"/>'
        f'<path d="M92 44 l8 -4 m-4 10 l8 0" stroke="#E04848" stroke-width="3"/>',
    )
    return svg(f'<g transform="translate({shake:.1f} 0)">{face}</g>' + puffs)


def gloomy(i: int) -> str:
    """우울: 머리 위 작은 비구름에서 비가 똑똑."""
    t = phase(i)
    drops = ""
    for k, x in enumerate((50, 64, 78)):
        p = (t + k / 3) % 1.0
        y = 26 + p * 10
        a = 1 - p * 0.8
        drops += f'<path d="M{x} {y:.1f} l-2 6" stroke="{BLUE}" stroke-width="3" opacity="{a:.2f}"/>'
    cloud = (
        f'<path d="M40 24 a10 10 0 0 1 8 -14 a14 14 0 0 1 26 -2 a10 10 0 0 1 14 10 a8 8 0 0 1 -2 6 z" '
        f'fill="#E3E8EF" stroke="{INK}" stroke-width="{STROKE}"/>'
    )
    face = _face(
        FACE_GLOOM,
        f'<path d="M42 72 q8 -6 16 0 M70 72 q8 -6 16 0" stroke="{INK}" stroke-width="4"/>'
        f'<path d="M52 98 q12 -8 24 0" stroke="{INK}" stroke-width="4"/>'
        f'<path d="M84 80 q3 8 0 10 q-3 -2 0 -10 z" fill="{BLUE}" stroke="{INK}" stroke-width="1.5"/>',
        cy=78,
    )
    return svg(face + cloud + drops)


def vacation(_i: int = 0) -> str:
    """연가: 섬 위 야자수와 해."""
    return svg(
        f'<circle cx="96" cy="30" r="14" fill="#FFD25E" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<ellipse cx="64" cy="104" rx="50" ry="12" fill="#9FD3F2" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M30 102 q34 -26 68 0 z" fill="#FFE2A6" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M60 92 q-4 -30 8 -52" stroke="{WOOD}" stroke-width="8"/>'
        f'<path d="M60 92 q-4 -30 8 -52" stroke="{INK}" stroke-width="1.5" opacity="0.5"/>'
        f'<path d="M68 40 q-22 -14 -40 4 q18 -6 40 -4 z" fill="#5CC08F" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M68 40 q20 -18 40 0 q-20 -6 -40 0 z" fill="#5CC08F" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<path d="M68 40 q-6 -22 10 -30 q-4 14 -10 30 z" fill="#46AE7C" stroke="{INK}" stroke-width="{STROKE}"/>'
        f'<circle cx="64" cy="46" r="4" fill="{WOOD}" stroke="{INK}" stroke-width="2"/>'
    )


def highlighter_yellow(_i: int = 0) -> str:
    """형광펜 줄(노랑): 칸 위에 그어진 반투명 형광펜 자국. 가로로 긴 띠 스티커."""
    w, h = 256, 48
    return svg(
        f'<path d="M10 14 q60 -6 120 0 t116 0 l2 20 q-60 6 -120 0 t-116 0 z" fill="#FFE45C" opacity="0.75"/>'
        f'<path d="M14 18 q60 -4 116 0 t112 0" stroke="#FFF6B0" stroke-width="3" opacity="0.8"/>',
        w,
        h,
    )


CATEGORIES = [
    ("work", "업무"), ("company", "회사 일정"), ("people", "인사·경조사"), ("appointment", "약속"),
    ("mood", "기분·컨디션"), ("status", "처리 상태"), ("leave", "휴가·근무"), ("celebrate", "기념일"),
    ("season", "계절·날씨"), ("lines", "선·테두리"),
]


def _sticker_table() -> dict[str, dict]:
    import sticker_v2_more as m

    A, P = True, False  # 움직임 / 정지
    rows = [
        # 업무
        ("deadline_flag", deadline_flag, A, "마감", "work"), ("important", m.important, A, "중요", "work"),
        ("meeting", m.meeting, P, "회의", "work"), ("business_trip", m.business_trip, P, "출장", "work"),
        ("report", m.report, P, "보고서", "work"), ("approval_stamp", m.approval_stamp, P, "결재", "work"),
        ("education", m.education, P, "교육", "work"), ("civil_call", m.civil_call, P, "민원 전화", "work"),
        # 회사 일정
        ("commute", m.commute, P, "출근", "company"), ("overtime", m.overtime, A, "야근", "company"),
        ("workshop", m.workshop, P, "워크숍", "company"), ("cheers", cheers, A, "회식", "company"),
        ("audit", m.audit, P, "감사", "company"), ("presentation", m.presentation, P, "발표", "company"),
        ("field_work", m.field_work, P, "외근", "company"), ("transfer", m.transfer, P, "전입·전출", "company"),
        # 인사·경조사
        ("promotion", promotion, A, "승진", "people"), ("medal", m.medal, P, "축하 메달", "people"),
        ("welcome", m.welcome, A, "환영", "people"), ("farewell", m.farewell, P, "송별", "people"),
        ("wedding", m.wedding, P, "결혼", "people"), ("birth", m.birth, P, "출산", "people"),
        ("condolence", m.condolence, P, "조문", "people"),
        # 약속
        ("meal", meal, P, "식사", "appointment"), ("lunchbox", m.lunchbox, P, "점심", "appointment"),
        ("tea_time", tea_time, A, "차 마시기", "appointment"), ("coffee", m.coffee, P, "커피", "appointment"),
        ("dessert", m.dessert, P, "디저트", "appointment"), ("beer", m.beer, P, "술 약속", "appointment"),
        ("handshake", m.handshake, P, "만남", "appointment"), ("phone_call", m.phone_call, P, "전화 약속", "appointment"),
        # 기분·컨디션
        ("happy", m.happy, P, "기쁨", "mood"), ("excited", m.excited, A, "신남", "mood"),
        ("angry", angry, A, "화남", "mood"), ("gloomy", gloomy, A, "우울", "mood"),
        ("sad", m.sad, P, "슬픔", "mood"), ("tired", m.tired, P, "피곤", "mood"),
        ("stress", m.stress, P, "스트레스", "mood"), ("worried", m.worried, P, "걱정", "mood"),
        ("sick_leave", m.sick_leave, P, "병가", "mood"), ("sick", m.sick, P, "아픔", "mood"),
        # 처리 상태
        ("done_check", done_check, A, "완료", "status"), ("in_progress", m.in_progress, A, "진행 중", "status"),
        ("on_hold", m.on_hold, P, "보류", "status"), ("cancelled", m.cancelled, P, "취소", "status"),
        # 휴가·근무
        ("vacation", vacation, P, "연가", "leave"), ("half_day", m.half_day, P, "반차", "leave"),
        ("night_duty", m.night_duty, P, "당직", "leave"), ("remote", m.remote, P, "재택", "leave"),
        # 기념일
        ("birthday_cake", m.birthday_cake, A, "생일", "celebrate"), ("gift", m.gift, P, "선물", "celebrate"),
        ("fireworks", m.fireworks, A, "축하", "celebrate"), ("heart", m.heart, A, "하트", "celebrate"),
        ("star", m.star, A, "별", "celebrate"),
        # 계절·날씨
        ("sun", m.sun, P, "해", "season"), ("rain_umbrella", m.rain_umbrella, P, "비", "season"),
        ("snowman", m.snowman, P, "눈사람", "season"), ("cherry_blossom", m.cherry_blossom, P, "벚꽃", "season"),
        ("maple_leaf", m.maple_leaf, P, "단풍", "season"),
        # 선·테두리
        ("highlighter_yellow", highlighter_yellow, P, "형광펜(노랑)", "lines"),
        ("highlighter_pink", m.highlighter_pink, P, "형광펜(분홍)", "lines"),
        ("highlighter_mint", m.highlighter_mint, P, "형광펜(민트)", "lines"),
        ("circle_hand", m.circle_hand, P, "동그라미", "lines"), ("box_outline", m.box_outline, P, "네모 테두리", "lines"),
        ("arrow_hand", m.arrow_hand, P, "화살표", "lines"),
    ]
    still_frame = {"cheers": 4, "done_check": FRAMES - 1, "fireworks": 5, "welcome": 2}
    # SVG 글자는 한글 글꼴을 못 찾아 네모로 나오므로, 글자는 그린 뒤 Qt 글꼴로 덧그린다: (글자, 색, 크기px, 중심 y)
    overlay_text = {"approval_stamp": ("결", RED, 50, 64)}
    wide = {"highlighter_yellow", "highlighter_pink", "highlighter_mint"}
    table: dict[str, dict] = {}
    for name, draw, animated, label, cat in rows:
        spec = {"draw": draw, "animated": animated, "label": label, "category": cat}
        if name in still_frame:
            spec["still"] = still_frame[name]
        if name in wide:
            spec["size"] = (256, 48)
        if name in overlay_text:
            spec["text"] = overlay_text[name]
        table[name] = spec
    return table


def render(svg_text: str, w: int, h: int, text: tuple | None = None) -> QImage:
    image = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, w, h))
    if text:
        from PySide6.QtGui import QColor, QFont

        from PySide6.QtGui import QFontDatabase

        glyph, color, px, cy = text
        # 화면 없이(offscreen) 그릴 때는 시스템 글꼴이 없으므로 앱에 들어 있는 Pretendard 를 직접 불러 쓴다
        font_file = Path(__file__).resolve().parent.parent / "taskcalendar" / "assets" / "fonts" / "Pretendard-Bold.ttf"
        families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font_file)))
        font = QFont(families[0] if families else "Malgun Gothic")
        font.setPixelSize(px)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRectF(0, cy - px, w, px * 2), Qt.AlignCenter, glyph)
    painter.end()
    return image


def build(out_dir: Path, only: set[str] | None = None) -> list[Path]:
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    stickers = _sticker_table()
    manifest = {
        "categories": [{"id": cid, "label": label} for cid, label in CATEGORIES],
        "stickers": [
            {"name": f"v2_{n}", "label": sp["label"], "category": sp["category"], "animated": sp["animated"],
             "still": sp.get("still", 0)}
            for n, sp in stickers.items()
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, spec in stickers.items():
        if only and name not in only:
            continue
        w, h = spec.get("size", (SIZE, SIZE))
        draw = spec["draw"]
        still = render(draw(spec.get("still", 0)), w, h, spec.get("text"))
        path = out_dir / f"v2_{name}.png"
        still.save(str(path))
        written.append(path)
        if spec["animated"]:
            sheet = QImage(w * FRAMES, h, QImage.Format_ARGB32_Premultiplied)
            sheet.fill(Qt.transparent)
            painter = QPainter(sheet)
            for i in range(FRAMES):
                painter.drawImage(i * w, 0, render(draw(i), w, h))
            painter.end()
            sheet_dir = out_dir / "sprites" / f"v2_{name}"
            sheet_dir.mkdir(parents=True, exist_ok=True)
            sheet_path = sheet_dir / f"v2_{name}_sheet.png"
            sheet.save(str(sheet_path))
            written.append(sheet_path)
    return written


if __name__ == "__main__":
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    target = Path(sys.argv[1])
    selected = None
    if "--only" in sys.argv:
        selected = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    for p in build(target, selected):
        print(p.name)
