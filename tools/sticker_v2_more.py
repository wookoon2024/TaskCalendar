"""스티커 v2 나머지 그림 (sticker_v2_gen.py 가 불러 쓴다). 그림체: 파스텔 + 진회색 얇은 외곽선."""
from __future__ import annotations

import math

from sticker_v2_gen import (
    BLUE, CREAM, FACE, FACE_GLOOM, FRAMES, GREEN, GREEN_DARK, INK, RED, STROKE, WOOD,
    _face, phase, svg, wave,
)

S = f'stroke="{INK}" stroke-width="{STROKE}"'
S2 = f'stroke="{INK}" stroke-width="2"'
YELLOW = "#FFD25E"
PINK = "#FF9EB8"
MINT = "#9ED8C6"
ORANGE = "#FFB067"
PURPLE = "#B9A6F2"
GRAY = "#C9C3BA"
SHADOW = '<ellipse cx="64" cy="114" rx="40" ry="5" fill="#E9E1D3"/>'
FONT = 'font-family="Malgun Gothic" font-weight="700"'


def star_path(cx: float, cy: float, r: float, inner: float = 0.45, points: int = 5) -> str:
    pts = []
    for k in range(points * 2):
        rad = r if k % 2 == 0 else r * inner
        ang = -math.pi / 2 + k * math.pi / points
        pts.append(f"{cx + rad * math.cos(ang):.1f} {cy + rad * math.sin(ang):.1f}")
    return "M" + " L".join(pts) + " Z"


def sparkle(cx: float, cy: float, r: float, color: str = YELLOW, opacity: float = 1.0) -> str:
    return (
        f'<path d="M{cx} {cy - r} Q{cx} {cy} {cx + r} {cy} Q{cx} {cy} {cx} {cy + r} Q{cx} {cy} {cx - r} {cy} '
        f'Q{cx} {cy} {cx} {cy - r} Z" fill="{color}" {S2} opacity="{opacity:.2f}"/>'
    )


def eyes_dot(y: int = 68) -> str:
    return f'<circle cx="50" cy="{y}" r="4.5" fill="{INK}"/><circle cx="78" cy="{y}" r="4.5" fill="{INK}"/>'


def cheeks() -> str:
    return '<ellipse cx="40" cy="82" rx="7" ry="4" fill="#FF9EAA" opacity="0.7"/><ellipse cx="88" cy="82" rx="7" ry="4" fill="#FF9EAA" opacity="0.7"/>'


# ── 업무 표시 ────────────────────────────────────────────────────────────

def important(i: int) -> str:
    on = i % 4 < 2
    glow = '<circle cx="64" cy="62" r="50" fill="#FFD3D3" opacity="0.6"/>' if on else ""
    return svg(
        SHADOW + glow
        + f'<circle cx="64" cy="62" r="40" fill="{RED}" {S}/>'
        + '<rect x="58" y="34" width="12" height="34" rx="6" fill="#FFFFFF"/>'
        + '<circle cx="64" cy="82" r="7" fill="#FFFFFF"/>'
    )


def meeting(_i: int = 0) -> str:
    return svg(
        f'<path d="M14 30 h64 a10 10 0 0 1 10 10 v26 a10 10 0 0 1 -10 10 h-36 l-14 12 v-12 h-14 a10 10 0 0 1 -10 -10 v-26 a10 10 0 0 1 10 -10 z" fill="{MINT}" {S}/>'
        f'<path d="M50 56 h54 a10 10 0 0 1 10 10 v22 a10 10 0 0 1 -10 10 h-6 v12 l-14 -12 h-34 a10 10 0 0 1 -10 -10 v-22 a10 10 0 0 1 10 -10 z" fill="{YELLOW}" {S}/>'
        f'<circle cx="66" cy="77" r="3.5" fill="{INK}"/><circle cx="80" cy="77" r="3.5" fill="{INK}"/><circle cx="94" cy="77" r="3.5" fill="{INK}"/>'
        f'<path d="M28 48 h34 M28 58 h22" stroke="#FFFFFF" stroke-width="5"/>'
    )


def business_trip(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<rect x="48" y="26" width="32" height="16" rx="6" {S}/>'
        + f'<rect x="20" y="38" width="88" height="66" rx="10" fill="{WOOD}" {S}/>'
        + f'<path d="M20 62 h88" {S}/>'
        + f'<rect x="56" y="56" width="16" height="12" rx="3" fill="{YELLOW}" {S2}/>'
        + '<path d="M30 48 v8 M98 48 v8" stroke="#FFFFFF" stroke-width="3" opacity="0.5"/>'
    )


def report(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<path d="M30 16 h48 l20 20 v74 a6 6 0 0 1 -6 6 h-62 a6 6 0 0 1 -6 -6 v-88 a6 6 0 0 1 6 -6 z" fill="#FFFFFF" {S}/>'
        + f'<path d="M78 16 v20 h20" fill="#E9E1D3" {S}/>'
        + f'<path d="M38 50 h40 M38 62 h48 M38 74 h30" stroke="#B9C7D6" stroke-width="5"/>'
        + f'<circle cx="84" cy="94" r="16" fill="{GREEN}" {S}/>'
        + '<path d="M76 94 l6 6 l10 -12" stroke="#FFFFFF" stroke-width="5"/>'
    )


def approval_stamp(_i: int = 0) -> str:
    return svg(
        f'<circle cx="64" cy="64" r="46" fill="#FFF1EE" stroke="{RED}" stroke-width="7"/>'
        f'<circle cx="64" cy="64" r="36" fill="none" stroke="{RED}" stroke-width="2.5" stroke-dasharray="6 5"/>'
    )


def education(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<path d="M14 40 q26 -12 50 2 v66 q-24 -14 -50 -2 z" fill="#FFFFFF" {S}/>'
        + f'<path d="M114 40 q-26 -12 -50 2 v66 q24 -14 50 -2 z" fill="#FFFFFF" {S}/>'
        + f'<path d="M24 56 q14 -6 30 0 M24 70 q14 -6 30 0 M74 56 q14 -6 30 0" stroke="#B9C7D6" stroke-width="4"/>'
        + f'<g transform="rotate(35 92 30)"><rect x="86" y="4" width="12" height="48" rx="2" fill="{YELLOW}" {S2}/>'
        + f'<path d="M86 52 l6 12 l6 -12 z" fill="{CREAM}" {S2}/><rect x="86" y="0" width="12" height="8" rx="2" fill="{PINK}" {S2}/></g>'
    )


def civil_call(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<rect x="24" y="58" width="80" height="48" rx="12" fill="{MINT}" {S}/>'
        + f'<path d="M20 52 q44 -36 88 0 l-8 14 q-36 -20 -72 0 z" fill="{GREEN}" {S}/>'
        + ''.join(f'<circle cx="{x}" cy="{y}" r="4" fill="#FFFFFF" {S2}/>' for x in (48, 64, 80) for y in (76, 92))
    )


# ── 회사 일정 ────────────────────────────────────────────────────────────

def commute(_i: int = 0) -> str:
    return svg(
        f'<path d="M40 10 L58 46 M88 10 L70 46" stroke="{BLUE}" stroke-width="6"/>'
        + f'<rect x="32" y="40" width="64" height="78" rx="10" fill="#FFFFFF" {S}/>'
        + f'<rect x="54" y="36" width="20" height="10" rx="3" fill="{GRAY}" {S2}/>'
        + f'<circle cx="64" cy="70" r="14" fill="{FACE}" {S2}/>'
        + f'<path d="M48 102 q16 -14 32 0" fill="{BLUE}" {S2}/>'
        + f'<path d="M44 108 h40" stroke="#B9C7D6" stroke-width="4"/>'
    )


def overtime(i: int) -> str:
    glow = 0.35 + 0.65 * (1 if i % 4 < 2 else 0.3)
    return svg(
        f'<path d="M98 14 a16 16 0 1 0 12 26 a13 13 0 1 1 -12 -26 z" fill="{YELLOW}" {S2}/>'
        + sparkle(24, 22, 6, "#FFFFFF", 0.9)
        + f'<rect x="18" y="38" width="80" height="54" rx="8" fill="#5B6B8C" {S}/>'
        + f'<rect x="26" y="46" width="64" height="38" rx="4" fill="#BFE3FF" opacity="{glow:.2f}"/>'
        + f'<path d="M34 56 h30 M34 66 h44 M34 76 h20" stroke="#FFFFFF" stroke-width="4" opacity="{glow:.2f}"/>'
        + f'<path d="M50 92 l-6 16 h28 l-6 -16" fill="{GRAY}" {S}/>'
        + f'<path d="M36 110 h44" {S}/>'
    )


def workshop(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<path d="M64 18 L112 108 H16 Z" fill="{ORANGE}" {S}/>'
        + f'<path d="M64 18 L64 108" {S}/>'
        + f'<path d="M64 60 L46 108 H82 Z" fill="#7A5A44" {S}/>'
        + f'<path d="M64 18 L56 6 M64 18 L72 6" {S}/>'
        + f'<path d="M100 40 v-16 l12 6 l-12 6" fill="{RED}" {S2}/>'
    )


def audit(_i: int = 0) -> str:
    return svg(
        f'<rect x="16" y="20" width="64" height="84" rx="6" fill="#FFFFFF" {S}/>'
        + f'<path d="M26 40 h44 M26 52 h36 M26 64 h40 M26 76 h24" stroke="#B9C7D6" stroke-width="5"/>'
        + f'<circle cx="78" cy="70" r="22" fill="#DDF0FF" opacity="0.85" {S}/>'
        + f'<path d="M94 86 L112 104" stroke="{WOOD}" stroke-width="10"/>'
        + f'<path d="M94 86 L112 104" stroke="{INK}" stroke-width="2" opacity="0.4"/>'
        + '<path d="M66 62 q6 -8 14 -6" stroke="#FFFFFF" stroke-width="4"/>'
    )


def presentation(_i: int = 0) -> str:
    return svg(
        f'<rect x="12" y="16" width="96" height="66" rx="6" fill="#FFFFFF" {S}/>'
        + f'<path d="M60 82 v18 M60 100 l-16 14 M60 100 l16 14" {S}/>'
        + f'<rect x="26" y="54" width="12" height="18" fill="{MINT}" {S2}/><rect x="46" y="42" width="12" height="30" fill="{YELLOW}" {S2}/>'
        + f'<rect x="66" y="32" width="12" height="40" fill="{PINK}" {S2}/>'
        + f'<path d="M84 46 L118 70" stroke="{WOOD}" stroke-width="5"/>'
        + f'<circle cx="84" cy="46" r="4" fill="{RED}" {S2}/>'
    )


def field_work(_i: int = 0) -> str:
    return svg(
        f'<path d="M12 40 l32 -12 l40 12 l32 -12 v72 l-32 12 l-40 -12 l-32 12 z" fill="#E7F3E2" {S}/>'
        + f'<path d="M44 28 v72 M84 40 v72" stroke="{INK}" stroke-width="2" opacity="0.4"/>'
        + f'<path d="M64 98 c-20 -22 -26 -34 -26 -46 a26 26 0 0 1 52 0 c0 12 -6 24 -26 46 z" fill="{RED}" {S}/>'
        + f'<circle cx="64" cy="52" r="10" fill="#FFFFFF" {S2}/>'
    )


def transfer(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<path d="M18 48 L64 32 L110 48 L64 64 Z" fill="#E8C49A" {S}/>'
        + f'<path d="M18 48 v46 l46 18 v-48 z" fill="#D9A877" {S}/>'
        + f'<path d="M110 48 v46 l-46 18 v-48 z" fill="#C9935F" {S}/>'
        + f'<path d="M40 40 l46 16 v14" stroke="#FFF1DA" stroke-width="6"/>'
        + f'<path d="M30 74 l12 4" stroke="{INK}" stroke-width="2" opacity="0.5"/>'
    )


# ── 인사·경조사 ──────────────────────────────────────────────────────────

def medal(_i: int = 0) -> str:
    return svg(
        f'<path d="M40 8 L56 54 L64 50 L72 54 L88 8 H74 L64 36 L54 8 Z" fill="{BLUE}" {S}/>'
        + f'<circle cx="64" cy="82" r="30" fill="{YELLOW}" {S}/>'
        + f'<circle cx="64" cy="82" r="21" fill="#FFE59A" {S2}/>'
        + f'<path d="{star_path(64, 83, 12)}" fill="#FFB43A" {S2}/>'
    )


def welcome(i: int) -> str:
    ang = wave(i, 18)
    return svg(
        f'<g transform="rotate({ang:.1f} 64 104)">'
        f'<path d="M44 104 v-40 a8 8 0 0 1 16 0 v-20 a8 8 0 0 1 16 0 v-6 a8 8 0 0 1 16 0 v40 a8 8 0 0 1 12 6 l-14 28 a20 20 0 0 1 -18 12 h-12 a16 16 0 0 1 -16 -16 z" fill="{FACE}" {S}/>'
        f'<path d="M60 44 v30 M76 38 v34" stroke="{INK}" stroke-width="2" opacity="0.5"/></g>'
        + f'<path d="M22 46 q-8 10 0 20 M14 40 q-12 16 0 32" stroke="{ORANGE}" stroke-width="4" opacity="{0.4 + 0.6 * abs(ang) / 18:.2f}"/>'
    )


def farewell(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<rect x="14" y="34" width="100" height="70" rx="8" fill="#FFFFFF" {S}/>'
        + f'<path d="M14 40 l50 36 l50 -36" fill="none" {S}/>'
        + f'<path d="M64 62 c-10 -12 -26 -2 -16 10 l16 14 l16 -14 c10 -12 -6 -22 -16 -10 z" fill="{PINK}" {S2}/>'
    )


def wedding(_i: int = 0) -> str:
    return svg(
        f'<circle cx="48" cy="74" r="28" fill="none" stroke="{INK}" stroke-width="12"/>'
        + f'<circle cx="48" cy="74" r="28" fill="none" stroke="{YELLOW}" stroke-width="7"/>'
        + f'<circle cx="80" cy="74" r="28" fill="none" stroke="{INK}" stroke-width="12"/>'
        + f'<circle cx="80" cy="74" r="28" fill="none" stroke="#E6E6EE" stroke-width="7"/>'
        + f'<path d="M72 30 l8 -12 l8 12 l-8 10 z" fill="#BDE7FF" {S2}/>'
        + sparkle(104, 28, 8, "#FFFFFF")
    )


def birth(_i: int = 0) -> str:
    def foot(x: float, y: float, rot: float) -> str:
        return (
            f'<g transform="translate({x} {y}) rotate({rot})">'
            f'<ellipse cx="0" cy="14" rx="14" ry="20" fill="{PINK}" {S}/>'
            + ''.join(f'<circle cx="{dx}" cy="{dy}" r="{r}" fill="{PINK}" {S2}/>' for dx, dy, r in ((-12, -12, 5), (-3, -16, 5), (6, -15, 4.5), (13, -10, 4)))
            + '</g>'
        )
    return svg(foot(44, 74, -12) + foot(86, 56, 12))


def condolence(_i: int = 0) -> str:
    petals = ''.join(
        f'<ellipse cx="64" cy="34" rx="7" ry="18" fill="#FFFFFF" {S2} transform="rotate({a} 64 50)"/>'
        for a in range(0, 360, 30)
    )
    return svg(
        f'<path d="M64 60 q-4 30 2 56" stroke="{GREEN_DARK}" stroke-width="5"/>'
        + f'<path d="M64 92 q-18 -8 -24 -22 q18 4 24 22 z" fill="#7CC59A" {S2}/>'
        + petals
        + f'<circle cx="64" cy="50" r="9" fill="#FFF3B8" {S2}/>'
    )


# ── 약속 ────────────────────────────────────────────────────────────────

def lunchbox(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<rect x="16" y="40" width="96" height="66" rx="12" fill="{RED}" {S}/>'
        + f'<rect x="24" y="48" width="80" height="50" rx="6" fill="#FFFFFF" {S2}/>'
        + f'<path d="M64 48 v50" {S2}/>'
        + f'<path d="M30 70 q10 -16 28 0 v22 h-28 z" fill="#FFFFFF" {S2}/>'
        + '<circle cx="44" cy="74" r="4" fill="#F26464"/>'
        + f'<circle cx="78" cy="66" r="7" fill="{YELLOW}" {S2}/><path d="M72 84 h22" stroke="#7CC59A" stroke-width="8"/>'
        + f'<path d="M30 34 L100 22" stroke="{WOOD}" stroke-width="5"/>'
    )


def coffee(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<path d="M34 34 L94 34 L86 112 H42 Z" fill="#FFFFFF" {S}/>'
        + f'<rect x="28" y="24" width="72" height="12" rx="4" fill="#7A5A44" {S}/>'
        + f'<path d="M37 60 H91 L88 86 H40 Z" fill="#C98A55" {S2}/>'
        + f'<path d="M58 66 q6 6 12 0 q-6 -6 -12 0 z" fill="#FFFFFF" opacity="0.7"/>'
        + f'<path d="M72 24 v-12" {S}/>'
    )


def dessert(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<path d="M20 64 L104 40 V96 L20 110 Z" fill="#FFE4C4" {S}/>'
        + f'<path d="M20 64 L104 40 L84 30 Z" fill="#FFFFFF" {S}/>'
        + f'<path d="M20 82 L104 64" stroke="{PINK}" stroke-width="7"/>'
        + f'<circle cx="80" cy="30" r="9" fill="{RED}" {S2}/><path d="M80 21 q4 -8 10 -8" {S2}/>'
    )


def beer(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<rect x="32" y="34" width="52" height="76" rx="8" fill="#FFC94D" {S}/>'
        + f'<path d="M84 50 h10 a10 10 0 0 1 10 10 v18 a10 10 0 0 1 -10 10 h-10" {S}/>'
        + f'<path d="M28 36 c4 -16 18 -16 22 -8 c4 -12 22 -12 26 -2 c6 -6 14 -2 12 8 z" fill="#FFFFFF" {S}/>'
        + '<path d="M46 50 v48 M60 50 v48" stroke="#FFFFFF" stroke-width="4" opacity="0.6"/>'
        + '<circle cx="70" cy="70" r="3" fill="#FFFFFF" opacity="0.8"/><circle cx="64" cy="86" r="2" fill="#FFFFFF" opacity="0.8"/>'
    )


def handshake(_i: int = 0) -> str:
    return svg(
        f'<path d="M6 58 L30 46 L40 64 L18 78 Z" fill="{BLUE}" {S}/>'
        + f'<path d="M122 58 L98 46 L88 64 L110 78 Z" fill="{GREEN}" {S}/>'
        + f'<path d="M30 50 L58 40 L82 52 L98 50 L104 70 L74 92 a8 8 0 0 1 -11 -2 l-28 -24 z" fill="{FACE}" {S}/>'
        + f'<path d="M58 40 L48 58 a7 7 0 0 0 12 4 L70 50" fill="{FACE}" {S}/>'
        + f'<path d="M62 84 l-8 -8 M70 76 l-8 -8 M78 70 l-8 -8" stroke="{INK}" stroke-width="2" opacity="0.6"/>'
    )


def phone_call(_i: int = 0) -> str:
    return svg(
        f'<rect x="38" y="14" width="52" height="100" rx="12" fill="#5B6B8C" {S}/>'
        + f'<rect x="44" y="26" width="40" height="70" rx="4" fill="#BFE3FF"/>'
        + f'<path d="M58 54 a6 6 0 0 1 6 -6 h0 a6 6 0 0 1 6 6 l-2 4 l4 6 l4 -2 a6 6 0 0 1 4 8 a10 10 0 0 1 -14 4 a22 22 0 0 1 -10 -14 z" fill="{GREEN}" {S2}/>'
        + f'<circle cx="64" cy="104" r="4" fill="#FFFFFF"/>'
        + f'<path d="M98 36 q10 8 0 20 M106 30 q16 14 0 32" stroke="{ORANGE}" stroke-width="4"/>'
    )


# ── 기분·컨디션 ─────────────────────────────────────────────────────────

def happy(_i: int = 0) -> str:
    return svg(_face(FACE, '<path d="M42 66 q8 -8 16 0 M70 66 q8 -8 16 0" stroke="#3F3A36" stroke-width="5"/>'
                     + cheeks() + f'<path d="M46 86 q18 20 36 0 z" fill="#FFFFFF" {S}/>'))


def excited(i: int) -> str:
    bounce = -abs(math.sin(phase(i) * 2 * math.pi)) * 10
    tw = (math.sin(phase(i) * 4 * math.pi) + 1) / 2
    face = _face(FACE,
                 f'<path d="{star_path(50, 66, 9)}" fill="#FFB43A" {S2}/><path d="{star_path(78, 66, 9)}" fill="#FFB43A" {S2}/>'
                 + cheeks() + f'<path d="M48 86 q16 18 32 0 z" fill="#FF7A7A" {S}/>')
    return svg(
        f'<ellipse cx="64" cy="118" rx="{30 + bounce:.1f}" ry="4" fill="#E9E1D3"/>'
        f'<g transform="translate(0 {bounce - 4:.1f})">{face}</g>'
        + sparkle(18, 30, 8, YELLOW, tw) + sparkle(110, 40, 6, PINK, 1 - tw)
    )


def sad(_i: int = 0) -> str:
    return svg(_face("#FFE3A6", f'<path d="M42 64 l14 -6 M86 64 l-14 -6" stroke="{INK}" stroke-width="4"/>' + eyes_dot(74)
                     + f'<path d="M50 98 q14 -10 28 0" stroke="{INK}" stroke-width="4"/>'
                     + f'<path d="M50 80 q-6 12 0 16 q6 -4 0 -16 z" fill="{BLUE}" {S2}/>'))


def tired(_i: int = 0) -> str:
    return svg(
        _face("#FFE9B8", f'<path d="M42 72 h16 M70 72 h16" stroke="{INK}" stroke-width="5"/>'
              + f'<path d="M42 78 q8 4 16 0 M70 78 q8 4 16 0" stroke="#B9AFA4" stroke-width="3"/>'
              + f'<ellipse cx="64" cy="96" rx="8" ry="5" fill="{INK}"/>', cy=76)
        + f'<path d="M84 16 h16 l-16 18 h16" stroke="{BLUE}" stroke-width="4"/>'
        + f'<path d="M106 6 h10 l-10 12 h10" stroke="{BLUE}" stroke-width="3"/>'
    )


def stress(_i: int = 0) -> str:
    scribble = (
        f'<path d="M36 22 c10 -14 18 10 28 -4 c10 -14 18 10 28 -4 M40 30 c8 -10 16 8 24 -4 c8 -10 16 8 24 -4" '
        f'stroke="{INK}" stroke-width="3"/>'
    )
    return svg(
        _face("#FFD0A6", f'<path d="M42 70 l10 -6 l-10 -6 M86 70 l-10 -6 l10 -6" stroke="{INK}" stroke-width="4"/>'
              + f'<path d="M46 94 l6 -6 l6 6 l6 -6 l6 6 l6 -6 l6 6" stroke="{INK}" stroke-width="4"/>', cy=78)
        + scribble
    )


def worried(_i: int = 0) -> str:
    return svg(_face(FACE, f'<path d="M42 58 l14 -6 M86 58 l-14 -6" stroke="{INK}" stroke-width="4"/>' + eyes_dot(70)
                     + f'<path d="M52 96 q12 -6 24 0" stroke="{INK}" stroke-width="4"/>'
                     + f'<path d="M98 40 q-8 12 0 16 q8 -4 0 -16 z" fill="#BFE3FF" {S2}/>'))


def sick_leave(_i: int = 0) -> str:
    return svg(
        _face("#FFD3C2", f'<path d="M42 66 q8 6 16 0 M70 66 q8 6 16 0" stroke="{INK}" stroke-width="4"/>'
              + '<ellipse cx="40" cy="80" rx="8" ry="5" fill="#FF7A7A" opacity="0.6"/><ellipse cx="88" cy="80" rx="8" ry="5" fill="#FF7A7A" opacity="0.6"/>'
              + f'<path d="M58 88 L104 74" stroke="#FFFFFF" stroke-width="7"/><path d="M58 88 L104 74" {S2}/>'
              + f'<circle cx="104" cy="74" r="5" fill="{RED}" {S2}/>', cy=66)
        + f'<path d="M10 104 q54 -22 108 0 v14 h-108 z" fill="{PURPLE}" {S}/>'
        + f'<path d="M30 100 l8 14 M70 92 l6 14" stroke="#FFFFFF" stroke-width="3" opacity="0.6"/>'
    )


def sick(_i: int = 0) -> str:
    return svg(_face(FACE_GLOOM, f'<path d="M44 62 q6 -6 12 0 M72 62 q6 -6 12 0" stroke="{INK}" stroke-width="4"/>'
                     + f'<path d="M36 78 h56 a4 4 0 0 1 4 4 v12 a16 16 0 0 1 -16 16 h-32 a16 16 0 0 1 -16 -16 v-12 a4 4 0 0 1 4 -4 z" fill="#FFFFFF" {S}/>'
                     + f'<path d="M24 74 l12 8 M104 74 l-12 8" {S2}/>'
                     + f'<path d="M44 90 h40 M44 98 h40" stroke="#C9D6E6" stroke-width="3"/>'
                     + f'<g transform="rotate(-30 92 44)"><rect x="80" y="38" width="26" height="12" rx="6" fill="#F7C9A6" {S2}/>'
                     + '<circle cx="90" cy="44" r="1.5" fill="#3F3A36"/><circle cx="96" cy="44" r="1.5" fill="#3F3A36"/></g>'))


# ── 처리 상태 ───────────────────────────────────────────────────────────

def in_progress(i: int) -> str:
    ang = phase(i) * 360
    return svg(
        SHADOW
        + f'<circle cx="64" cy="62" r="44" fill="#FFFFFF" {S}/>'
        + f'<circle cx="64" cy="62" r="44" fill="none" stroke="{BLUE}" stroke-width="6" stroke-dasharray="{276 * (0.25 + phase(i) * 0.75):.0f} 276" transform="rotate(-90 64 62)"/>'
        + ''.join(f'<circle cx="{64 + 34 * math.cos(math.radians(a)):.1f}" cy="{62 + 34 * math.sin(math.radians(a)):.1f}" r="2.5" fill="{INK}"/>' for a in range(0, 360, 90))
        + f'<path d="M64 62 L64 38" stroke="{INK}" stroke-width="5" transform="rotate({ang:.0f} 64 62)"/>'
        + f'<path d="M64 62 L80 62" stroke="{INK}" stroke-width="5" transform="rotate({ang / 12:.0f} 64 62)"/>'
        + f'<circle cx="64" cy="62" r="5" fill="{RED}" {S2}/>'
    )


def on_hold(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<circle cx="64" cy="62" r="44" fill="{ORANGE}" {S}/>'
        + '<rect x="46" y="40" width="12" height="44" rx="5" fill="#FFFFFF"/><rect x="70" y="40" width="12" height="44" rx="5" fill="#FFFFFF"/>'
    )


def cancelled(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<circle cx="64" cy="62" r="44" fill="#D9D4CC" {S}/>'
        + '<path d="M46 44 L82 80 M82 44 L46 80" stroke="#FFFFFF" stroke-width="12"/>'
    )


# ── 휴가·근무 ───────────────────────────────────────────────────────────

def half_day(_i: int = 0) -> str:
    return svg(
        f'<path d="M24 74 a40 40 0 0 1 80 0 z" fill="{YELLOW}" {S}/>'
        + ''.join(f'<path d="M{64 + 50 * math.cos(math.radians(a)):.1f} {74 - 50 * math.sin(math.radians(a)):.1f} L{64 + 60 * math.cos(math.radians(a)):.1f} {74 - 60 * math.sin(math.radians(a)):.1f}" stroke="{ORANGE}" stroke-width="5"/>' for a in (20, 55, 90, 125, 160))
        + f'<path d="M10 74 h108" {S}/>'
        + f'<path d="M20 90 h88 M34 102 h60" stroke="{BLUE}" stroke-width="5"/>'
    )


def night_duty(_i: int = 0) -> str:
    return svg(
        f'<path d="M70 16 a44 44 0 1 0 40 62 a36 36 0 1 1 -40 -62 z" fill="{YELLOW}" {S}/>'
        + f'<path d="{star_path(100, 30, 10)}" fill="#FFFFFF" {S2}/>'
        + f'<path d="{star_path(32, 24, 6)}" fill="#FFFFFF" {S2}/>'
        + f'<circle cx="50" cy="70" r="3" fill="{INK}" opacity="0.4"/>'
    )


def remote(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<path d="M14 58 L64 18 L114 58" fill="none" stroke="{INK}" stroke-width="5"/>'
        + f'<path d="M24 52 V110 H104 V52 L64 22 Z" fill="#FFE2A6" {S}/>'
        + f'<rect x="38" y="66" width="52" height="32" rx="4" fill="#5B6B8C" {S2}/>'
        + f'<rect x="42" y="70" width="44" height="22" fill="#BFE3FF"/>'
        + f'<path d="M32 104 h64" stroke="{INK}" stroke-width="4"/>'
    )


# ── 기념일 ──────────────────────────────────────────────────────────────

def birthday_cake(i: int) -> str:
    flick = wave(i, 3)
    return svg(
        SHADOW
        + f'<rect x="20" y="66" width="88" height="44" rx="8" fill="#FFE4C4" {S}/>'
        + f'<path d="M20 78 q11 10 22 0 q11 10 22 0 q11 10 22 0 q11 10 22 0" fill="none" stroke="{PINK}" stroke-width="7"/>'
        + f'<rect x="30" y="44" width="68" height="26" rx="6" fill="#FFFFFF" {S}/>'
        + ''.join(
            f'<rect x="{x - 3}" y="26" width="6" height="18" fill="{c}" {S2}/>'
            f'<path d="M{x} {10 + flick if k % 2 == 0 else 10 - flick:.1f} q6 8 0 14 q-6 -6 0 -14 z" fill="#FFB43A" {S2}/>'
            for k, (x, c) in enumerate(((48, BLUE), (64, PINK), (80, MINT)))
        )
    )


def gift(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<rect x="24" y="54" width="80" height="56" rx="6" fill="{PINK}" {S}/>'
        + f'<rect x="18" y="40" width="92" height="18" rx="5" fill="#FFB8CB" {S}/>'
        + f'<path d="M56 40 h16 v70 h-16 z" fill="{YELLOW}" {S2}/>'
        + f'<path d="M64 40 c-22 -24 -34 2 -14 0 z M64 40 c22 -24 34 2 14 0 z" fill="{YELLOW}" {S}/>'
    )


def fireworks(i: int) -> str:
    p = phase(i)
    r = 10 + p * 40
    a = max(0.0, 1 - p * 0.9)
    rays = ''.join(
        f'<path d="M{64 + r * 0.5 * math.cos(math.radians(d)):.1f} {58 + r * 0.5 * math.sin(math.radians(d)):.1f} '
        f'L{64 + r * math.cos(math.radians(d)):.1f} {58 + r * math.sin(math.radians(d)):.1f}" '
        f'stroke="{c}" stroke-width="5" opacity="{a:.2f}"/>'
        for d, c in zip(range(0, 360, 30), [RED, YELLOW, BLUE, PINK, GREEN, ORANGE] * 2)
    )
    dots = ''.join(
        f'<circle cx="{64 + (r + 6) * math.cos(math.radians(d + 15)):.1f}" cy="{58 + (r + 6) * math.sin(math.radians(d + 15)):.1f}" r="3" fill="{YELLOW}" opacity="{a:.2f}"/>'
        for d in range(0, 360, 60)
    )
    return svg(rays + dots + f'<circle cx="64" cy="58" r="{max(2, 8 - p * 8):.1f}" fill="#FFFFFF" {S2} opacity="{a:.2f}"/>')


def heart(i: int) -> str:
    beat = 1.0 + (0.12 if i in (1, 3) else 0.04 if i in (2,) else 0.0)
    return svg(
        SHADOW
        + f'<g transform="translate(64 64) scale({beat}) translate(-64 -64)">'
        f'<path d="M64 104 C20 76 14 52 26 38 c12 -14 32 -10 38 6 c6 -16 26 -20 38 -6 c12 14 6 38 -38 66 z" fill="#FF7AA2" {S}/>'
        f'<ellipse cx="42" cy="50" rx="8" ry="5" fill="#FFFFFF" opacity="0.7" transform="rotate(-30 42 50)"/></g>'
    )


def star(i: int) -> str:
    tw = (math.sin(phase(i) * 2 * math.pi) + 1) / 2
    return svg(
        SHADOW
        + f'<path d="{star_path(64, 62, 46, 0.48)}" fill="{YELLOW}" {S}/>'
        + f'<path d="{star_path(64, 62, 26, 0.48)}" fill="#FFE89A"/>'
        + sparkle(106, 22, 6 + 6 * tw, "#FFFFFF", 0.3 + 0.7 * tw)
        + sparkle(22, 92, 5 + 5 * (1 - tw), "#FFFFFF", 0.3 + 0.7 * (1 - tw))
    )


# ── 계절·날씨 ───────────────────────────────────────────────────────────

def sun(_i: int = 0) -> str:
    rays = ''.join(
        f'<path d="M{64 + 38 * math.cos(math.radians(a)):.1f} {64 + 38 * math.sin(math.radians(a)):.1f} L{64 + 54 * math.cos(math.radians(a)):.1f} {64 + 54 * math.sin(math.radians(a)):.1f}" stroke="{ORANGE}" stroke-width="7"/>'
        for a in range(0, 360, 45)
    )
    return svg(rays + f'<circle cx="64" cy="64" r="28" fill="{YELLOW}" {S}/>' + cheeks().replace("82", "70") + eyes_dot(62))


def rain_umbrella(_i: int = 0) -> str:
    drops = ''.join(f'<path d="M{x} {y} l-3 8" stroke="{BLUE}" stroke-width="3"/>' for x, y in ((20, 20), (40, 10), (100, 14), (114, 34), (16, 50)))
    return svg(
        drops
        + f'<path d="M14 66 a50 44 0 0 1 100 0 q-12 -10 -25 0 q-12 -10 -25 0 q-12 -10 -25 0 q-12 -10 -25 0 z" fill="{BLUE}" {S}/>'
        + f'<path d="M64 22 v80 a10 10 0 0 1 -20 0" {S}/>'
        + f'<path d="M40 36 q-8 12 -8 28 M88 36 q8 12 8 28" stroke="#FFFFFF" stroke-width="3" opacity="0.5"/>'
    )


def snowman(_i: int = 0) -> str:
    return svg(
        SHADOW
        + f'<circle cx="64" cy="84" r="30" fill="#FFFFFF" {S}/>'
        + f'<circle cx="64" cy="42" r="22" fill="#FFFFFF" {S}/>'
        + f'<rect x="44" y="14" width="40" height="10" rx="4" fill="{RED}" {S2}/><rect x="52" y="0" width="24" height="18" rx="4" fill="{RED}" {S2}/>'
        + '<circle cx="56" cy="40" r="3" fill="#3F3A36"/><circle cx="72" cy="40" r="3" fill="#3F3A36"/>'
        + f'<path d="M64 46 l12 4 l-12 2 z" fill="{ORANGE}" {S2}/>'
        + f'<path d="M44 62 q20 10 40 0" stroke="{RED}" stroke-width="7"/>'
        + '<circle cx="64" cy="80" r="3" fill="#3F3A36"/><circle cx="64" cy="94" r="3" fill="#3F3A36"/>'
    )


def cherry_blossom(_i: int = 0) -> str:
    petals = ''.join(
        f'<path d="M64 62 c-14 -16 -12 -36 0 -38 c12 2 14 22 0 38 z" fill="#FFC2D4" {S} transform="rotate({a} 64 62)"/>'
        for a in range(0, 360, 72)
    )
    return svg(petals + f'<circle cx="64" cy="62" r="9" fill="#FFE07A" {S2}/>'
               + ''.join(f'<path d="M64 62 L{64 + 14 * math.cos(math.radians(a)):.1f} {62 + 14 * math.sin(math.radians(a)):.1f}" stroke="#E5739A" stroke-width="2"/>' for a in range(18, 360, 72)))


def maple_leaf(_i: int = 0) -> str:
    leaf = (
        "M64 14 L72 38 L92 30 L86 52 L110 54 L92 70 L100 80 L74 78 L70 98 L64 92 L58 98 L54 78 "
        "L28 80 L36 70 L18 54 L42 52 L36 30 L56 38 Z"
    )
    return svg(
        f'<path d="M64 92 L64 118" stroke="{WOOD}" stroke-width="5"/>'
        + f'<path d="{leaf}" fill="#FF8A4D" {S}/>'
        + f'<path d="M64 30 V88 M64 66 L86 56 M64 66 L42 56" stroke="#D9622C" stroke-width="2.5"/>'
    )


# ── 선·테두리 ───────────────────────────────────────────────────────────

def _highlighter(color: str, shine: str) -> str:
    return svg(
        f'<path d="M10 14 q60 -6 120 0 t116 0 l2 20 q-60 6 -120 0 t-116 0 z" fill="{color}" opacity="0.75"/>'
        f'<path d="M14 18 q60 -4 116 0 t112 0" stroke="{shine}" stroke-width="3" opacity="0.8"/>',
        256, 48,
    )


def highlighter_pink(_i: int = 0) -> str:
    return _highlighter("#FF9EC4", "#FFD9E8")


def highlighter_mint(_i: int = 0) -> str:
    return _highlighter("#7FE0BF", "#D4F7EA")


def circle_hand(_i: int = 0) -> str:
    return svg(
        '<path d="M70 18 C30 14 12 44 18 74 C24 104 70 116 98 98 C122 82 116 40 86 26 C70 18 50 20 40 26" '
        f'stroke="{RED}" stroke-width="6"/>'
    )


def box_outline(_i: int = 0) -> str:
    return svg(
        f'<rect x="10" y="22" width="108" height="84" rx="12" stroke="{GREEN}" stroke-width="6" stroke-dasharray="14 8"/>'
    )


def arrow_hand(_i: int = 0) -> str:
    return svg(
        f'<path d="M14 92 C40 60 70 46 104 40" stroke="{RED}" stroke-width="7"/>'
        f'<path d="M84 26 L106 40 L88 60" stroke="{RED}" stroke-width="7"/>'
    )
