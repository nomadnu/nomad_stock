"""벡터화 백테스트 엔진.

핵심 원칙:
1) 미래 참조 금지: t 시점 신호는 t+1 시점 시가/종가에 체결된다(1봉 지연).
2) 거래비용 반영: 포지션이 바뀔 때 수수료+세금을 뺀다.
3) 결과는 자산곡선(equity curve)과 거래내역으로 남긴다.

단순 일봉, 단일 종목, 풀매수/현금(0~1 비중) 기준의 교과서적 구현.
멀티 종목 포트폴리오는 다음 단계에서 확장한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..strategy.base import Strategy


@dataclass
class BacktestResult:
    equity: pd.Series          # 누적 자산곡선 (초기자본=1.0 기준 정규화 아님, 실제 금액)
    returns: pd.Series         # 일별 전략 수익률
    position: pd.Series        # 실제 보유 포지션(체결 반영, 1봉 지연)
    trades: pd.DataFrame       # 거래가 발생한 날의 내역
    initial_cash: float

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1])


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    initial_cash: float = 10_000_000,
    fee: float = 0.00015,     # 편도 수수료 0.015% (증권사 기본 가정)
    tax: float = 0.0020,      # 국내 매도세 0.20% (2024년 기준, 매도 시에만)
    slippage: float = 0.0,    # 슬리피지 (체결 불리, 비율)
) -> BacktestResult:
    """전략을 일봉 데이터에 적용해 백테스트한다.

    체결 모델: t일 종가 기준 신호 → t+1일 종가에 체결(보수적).
    """
    df = df.sort_index()
    close = df["Close"].astype(float)

    # 1) 전략 신호(목표 포지션). 미래 참조 방지 위해 1봉 지연시켜 '실제 포지션'으로.
    target = strategy.generate_signals(df).reindex(close.index).fillna(0.0)
    position = target.shift(1).fillna(0.0)   # 오늘 들고 있는 포지션은 어제 신호로 결정

    # 2) 자산 변화
    asset_ret = close.pct_change().fillna(0.0)        # 종목 일별 수익률
    gross_ret = position * asset_ret                  # 포지션 반영 수익률(비용 전)

    # 3) 거래비용: 포지션 변화량 * (수수료+슬리피지), 매도분엔 세금 추가
    pos_change = position.diff().fillna(position)      # 첫날 진입도 거래로 계산
    turnover = pos_change.abs()
    cost = turnover * (fee + slippage)
    sell_turnover = (-pos_change).clip(lower=0.0)      # 비중이 줄어든 만큼이 매도
    cost = cost + sell_turnover * tax

    net_ret = gross_ret - cost
    equity = initial_cash * (1.0 + net_ret).cumprod()

    # 4) 거래내역 (포지션이 바뀐 날만)
    trade_days = pos_change[pos_change != 0.0]
    trades = pd.DataFrame(
        {
            "price": close.reindex(trade_days.index),
            "pos_change": trade_days,
            "new_position": position.reindex(trade_days.index),
            "cost": cost.reindex(trade_days.index),
        }
    )

    return BacktestResult(
        equity=equity,
        returns=net_ret,
        position=position,
        trades=trades,
        initial_cash=initial_cash,
    )
