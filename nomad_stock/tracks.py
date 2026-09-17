"""펀더멘털 6트랙 등록부 (지침서 v1.4).

변수는 '필터 강도' 하나. 미국(S&P500)·한국(코스피200) 각 3강도(엄선/중간/폭넓게).
알림·대시보드는 여기 label/flag를 써서 '어느 트랙인지' 먼저 밝힌다.
"""
from __future__ import annotations

# 순서 = 대시보드·알림 표시 순서
TRACKS: dict[str, dict] = {
    "us_strict": {"market": "US", "strength": "strict", "label": "미국 A · 엄선", "flag": "🇺🇸", "color": "#4c8dff"},
    "us_mid":    {"market": "US", "strength": "mid",    "label": "미국 B · 균형", "flag": "🇺🇸", "color": "#5ac8fa"},
    "us_loose":  {"market": "US", "strength": "loose",  "label": "미국 C · 폭넓게", "flag": "🇺🇸", "color": "#63d29b"},
    "kr_strict": {"market": "KR", "strength": "strict", "label": "한국 1 · 엄선", "flag": "🇰🇷", "color": "#c07cff"},
    "kr_mid":    {"market": "KR", "strength": "mid",    "label": "한국 2 · 균형", "flag": "🇰🇷", "color": "#ffb454"},
    "kr_loose":  {"market": "KR", "strength": "loose",  "label": "한국 3 · 폭넓게", "flag": "🇰🇷", "color": "#f0776a"},
}

STRENGTH_KO = {"strict": "엄선", "mid": "균형", "loose": "폭넓게"}


def tag(tid: str) -> str:
    """알림 머리표. 예: '🇺🇸 [미국 B · 균형]'."""
    t = TRACKS[tid]
    return f"{t['flag']} [{t['label']}]"


def is_us(tid: str) -> bool:
    return TRACKS[tid]["market"] == "US"


def us_tracks() -> list[str]:
    return [k for k, v in TRACKS.items() if v["market"] == "US"]


def kr_tracks() -> list[str]:
    return [k for k, v in TRACKS.items() if v["market"] == "KR"]
