"""전략 인터페이스.

전략은 가격 데이터를 받아 매 시점의 '목표 포지션'을 신호로 낸다.
  +1 = 풀매수(보유),  0 = 현금(청산)
나중에 -1(공매도)이나 0~1 비중도 확장 가능하도록 float을 허용한다.

이 인터페이스 하나만 지키면, 같은 전략을 백테스트 엔진과
실거래/모의투자(KIS) 실행기 양쪽에 그대로 꽂을 수 있다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """모든 전략의 부모 클래스."""

    name: str = "base"

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """OHLCV DataFrame을 받아 목표 포지션 시계열을 반환한다.

        Returns
        -------
        pd.Series (index는 df.index와 동일, 값은 목표 포지션 -1.0 ~ 1.0)
        주의: t 시점 신호는 t 시점 '종가까지의 정보'만으로 만들어야 한다
        (미래 참조 금지). 엔진은 신호를 1봉 지연시켜 다음 날 체결한다.
        """
        raise NotImplementedError
