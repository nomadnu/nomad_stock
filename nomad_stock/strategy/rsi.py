"""RSI 평균회귀 전략.

RSI(상대강도지수)가 과매도(oversold) 아래로 내려가면 매수,
과매수/중립선(exit_level) 위로 올라오면 청산.
종가 기반이라 일봉 백테스트 엔진과 실거래에 그대로 적용된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder 방식 RSI (0~100)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder의 지수이동평균 (alpha = 1/period)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)  # 초기 구간은 중립


class RsiStrategy(Strategy):
    def __init__(self, period: int = 14, oversold: float = 30, exit_level: float = 50):
        if not (0 < oversold < exit_level < 100):
            raise ValueError("0 < oversold < exit_level < 100 이어야 합니다.")
        self.period = period
        self.oversold = oversold
        self.exit_level = exit_level
        self.name = f"RSI{period}({oversold:.0f}/{exit_level:.0f})"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        r = rsi(df["Close"], self.period)
        # 과매도 진입(1), 중립선 회복 시 청산(0), 그 사이는 직전 상태 유지
        signal = pd.Series(np.nan, index=df.index)
        signal[r < self.oversold] = 1.0
        signal[r > self.exit_level] = 0.0
        signal = signal.ffill().fillna(0.0)
        signal.name = "target_position"
        return signal
