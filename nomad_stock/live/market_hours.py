"""국내 증시 운영시간 판단.

정규장: 평일 09:00 ~ 15:30 (KST).
공휴일은 별도 캘린더가 필요하므로 여기서는 '요일+시간'만 본다.
(주문이 장중에만 체결되므로, 장 밖 실행을 사전에 막아 깔끔한 안내를 준다.)
"""
from __future__ import annotations

from datetime import datetime, time

_OPEN = time(9, 0)
_CLOSE = time(15, 30)


def is_market_open(now: datetime | None = None) -> bool:
    """지금이 정규장 시간인지 여부 (공휴일 미반영)."""
    now = now or datetime.now()
    if now.weekday() >= 5:  # 토(5), 일(6)
        return False
    return _OPEN <= now.time() <= _CLOSE


def market_status(now: datetime | None = None) -> str:
    """사람이 읽을 상태 문자열."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "장 휴장 (주말)"
    if now.time() < _OPEN:
        return f"장 시작 전 (개장 {_OPEN.strftime('%H:%M')})"
    if now.time() > _CLOSE:
        return f"장 마감 (마감 {_CLOSE.strftime('%H:%M')})"
    return "정규장 진행 중"
