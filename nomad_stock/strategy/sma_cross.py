"""이동평균 교차(Golden/Dead Cross) 전략 - 샘플.

단기 이동평균이 장기 이동평균 위로 올라가면 매수(목표 포지션 1),
아래로 내려가면 청산(목표 포지션 0).
가장 고전적인 추세추종 전략으로, 엔진 동작 검증용 기본 예제다.
"""
from __future__ import annotations

import pandas as pd

from .base import Strategy


class SmaCrossStrategy(Strategy):
    def __init__(self, fast: int = 20, slow: int = 60):
        if fast >= slow:
            raise ValueError("fast 기간은 slow보다 짧아야 합니다.")
        self.fast = fast
        self.slow = slow
        self.name = f"SMA{fast}x{slow}"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["Close"]
        fast_ma = close.rolling(self.fast).mean()
        slow_ma = close.rolling(self.slow).mean()

        # 단기 > 장기 → 보유(1), 아니면 현금(0)
        signal = (fast_ma > slow_ma).astype(float)
        # 이동평균이 아직 안 채워진 구간(NaN)은 현금 유지
        signal[slow_ma.isna()] = 0.0
        signal.name = "target_position"
        return signal
