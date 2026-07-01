"""볼린저밴드 평균회귀 전략.

중심선 = N일 이동평균, 밴드 = 중심선 ± num_std * N일 표준편차.
종가가 하단밴드 아래로 내려가면(과매도) 매수, 중심선 위로 회복하면 청산.
종가 기반이라 일봉 백테스트 엔진과 실거래에 그대로 적용된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class BollingerStrategy(Strategy):
    def __init__(self, period: int = 20, num_std: float = 2.0):
        if period < 2 or num_std <= 0:
            raise ValueError("period>=2, num_std>0 이어야 합니다.")
        self.period = period
        self.num_std = num_std
        self.name = f"BB{period}({num_std:g}σ)"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["Close"]
        mid = close.rolling(self.period).mean()
        std = close.rolling(self.period).std()
        lower = mid - self.num_std * std

        # 하단 이탈 시 진입(1), 중심선 회복 시 청산(0), 사이는 직전 상태 유지
        signal = pd.Series(np.nan, index=df.index)
        signal[close < lower] = 1.0
        signal[close > mid] = 0.0
        signal = signal.ffill().fillna(0.0)
        # 밴드가 아직 계산 안 된 초기 구간은 현금
        signal[mid.isna()] = 0.0
        signal.name = "target_position"
        return signal
