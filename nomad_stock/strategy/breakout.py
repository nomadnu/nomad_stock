"""변동성 돌파 전략 (Larry Williams) + 전용 장중 백테스트.

규칙:
  목표가(t) = 시가(t) + k * (고가(t-1) - 저가(t-1))
  당일 고가가 목표가를 넘으면 → 목표가에 매수(돌파 진입), 당일 종가에 청산.
  오버나이트 미보유(다음 날로 포지션을 넘기지 않음).

이 전략은 '당일 시가→종가' 수익 구조라 종가-종가 일봉 엔진과 맞지 않는다.
그래서 여기 전용 백테스트(run_breakout_backtest)를 둔다.

실거래는 장중 가격이 목표가를 돌파하는 순간을 잡아야 하므로,
하루 1회(마감 무렵) 실행하는 현재 스케줄러로는 충실히 재현되지 않는다.
→ 현재 단계에서는 '백테스트 전용'. 장중 모니터링은 추후 확장.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.engine import BacktestResult


def run_breakout_backtest(
    df: pd.DataFrame,
    k: float = 0.5,
    initial_cash: float = 10_000_000,
    fee: float = 0.00015,
    tax: float = 0.0020,
    slippage: float = 0.0,
) -> BacktestResult:
    """변동성 돌파 전용 백테스트.

    각 날: 돌파 성공 시 (목표가 → 종가) 수익을 얻고, 비용은 진입+청산 양쪽에 부과.
    오버나이트 미보유이므로 매일 진입/청산이 독립적이다.
    """
    df = df.sort_index()
    prev_range = (df["High"].shift(1) - df["Low"].shift(1))
    target_price = df["Open"] + k * prev_range

    entered = df["High"] >= target_price          # 당일 돌파 성공 여부
    entered = entered.fillna(False)

    # 돌파한 날의 수익률 = 종가/목표가 - 1, 비용(진입+청산 수수료 + 매도세) 차감
    raw_ret = (df["Close"] / target_price - 1.0)
    cost = (2 * (fee + slippage) + tax)           # 진입+청산 왕복 비용
    day_ret = np.where(entered, raw_ret - cost, 0.0)
    day_ret = pd.Series(day_ret, index=df.index).fillna(0.0)

    equity = initial_cash * (1.0 + day_ret).cumprod()

    # 거래내역: 돌파한 날만
    trade_days = entered[entered]
    trades = pd.DataFrame(
        {
            "target_price": target_price.reindex(trade_days.index),
            "close": df["Close"].reindex(trade_days.index),
            "day_return": day_ret.reindex(trade_days.index),
        }
    )

    return BacktestResult(
        equity=equity,
        returns=day_ret,
        position=entered.astype(float),  # 그날 진입했으면 1(장중만 보유)
        trades=trades,
        initial_cash=initial_cash,
    )
