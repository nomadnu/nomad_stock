"""주간 보유 재평가(리밸런싱) - 지침서 v0.4 §3-2.

매수 때 쓴 지표(60일선·볼린저)를 그대로 재활용해, 보유 종목의 매수 근거가
아직 유효한지 점검한다. 손절(장중 자동)과 달리 이건 정기 점검 + 승인 기반 매도.

판정:
  - 60일선 아래 이탈       → 매도검토 (추세 무너짐)
  - 60일선 위지만 볼린저 중심선 아래 → 주의 (강세 약해짐)
  - 60일선 위 + 중심선 위  → 보유권장
자동 매도 아님. 봇은 판정만 하고, 매도는 정미님 승인.
"""
from __future__ import annotations

from . import rules
from .data.loader import load_ohlcv

# 판정 상태
HOLD = "보유권장"
WATCH = "주의"
SELL = "매도검토"


def evaluate_holding(code: str) -> tuple[str, str]:
    """보유 종목 하나를 재평가해 (상태, 사유)를 반환."""
    try:
        df = load_ohlcv(code, start="2024-01-01", use_cache=False)
    except Exception as e:
        return WATCH, f"데이터 조회 실패({e})"
    if len(df) < rules.MA_TREND + 5:
        return WATCH, "데이터 부족"
    close = df["Close"]
    last = close.iloc[-1]
    ma60 = close.rolling(rules.MA_TREND).mean().iloc[-1]
    mid20 = close.rolling(20).mean().iloc[-1]  # 볼린저 중심선

    if last < ma60:
        return SELL, "60일선 아래로 이탈 (추세 무너짐)"
    if last < mid20:
        return WATCH, "60일선 위지만 볼린저 강세 약해짐"
    return HOLD, "60일선 위·강세 유지"


def review_holdings(holdings: list[dict]) -> list[dict]:
    """보유내역 전체를 재평가. 각 항목에 status/reason 추가해 반환."""
    out = []
    for h in holdings:
        status, reason = evaluate_holding(h["symbol"])
        out.append({**h, "status": status, "reason": reason})
    return out
