"""손절/익절을 반영하는 이벤트 루프 백테스트.

기본 엔진(engine.py)은 종가-종가 벡터화라 장중 손절/익절을 표현할 수 없다.
여기서는 하루씩 진행하며 당일 고가/저가로 손절·익절 체결을 판정한다.
  - 진입가 = 포지션 시작 직전 종가 (기본 엔진과 동일한 1봉 지연 가정)
  - 보유 중 당일 저가 ≤ 손절가 → 손절가(갭하락이면 시가)에 청산
  - 보유 중 당일 고가 ≥ 익절가 → 익절가(갭상승이면 시가)에 청산
  - 손절/익절 청산 후에는 전략 신호가 한 번 0으로 돌아왔다가 다시 1이 될 때까지
    재진입을 막는다(같은 자리 반복 진입 방지).
손절/익절을 0으로 두면 기본 엔진과 사실상 동일한 결과를 낸다.
"""
from __future__ import annotations

import pandas as pd

from ..strategy.base import Strategy
from .engine import BacktestResult


def run_backtest_with_risk(
    df: pd.DataFrame,
    strategy: Strategy,
    stop_loss: float = 0.0,      # 0.05 = -5% 손절 (0이면 끔)
    take_profit: float = 0.0,    # 0.10 = +10% 익절 (0이면 끔)
    initial_cash: float = 10_000_000,
    fee: float = 0.00015,
    tax: float = 0.0020,
    slippage: float = 0.0,
) -> BacktestResult:
    df = df.sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    open_ = df["Open"].astype(float)

    target = strategy.generate_signals(df).reindex(close.index).fillna(0.0)
    desired = target.shift(1).fillna(0.0)   # 1봉 지연된 목표 포지션

    idx = close.index
    rets = pd.Series(0.0, index=idx)
    actual_pos = pd.Series(0.0, index=idx)
    trades = []

    buy_cost = fee + slippage
    sell_cost = fee + slippage + tax

    holding = False
    entry = 0.0
    block = False  # 손절/익절 청산 후 재진입 차단

    for k in range(1, len(idx)):
        t = idx[k]
        c_prev, c, h, low_k, o = (
            close.iloc[k - 1], close.iloc[k], high.iloc[k], low.iloc[k], open_.iloc[k]
        )
        want = desired.iloc[k]
        if want == 0.0:
            block = False  # 신호가 청산으로 돌아오면 차단 해제

        r = 0.0
        # 1) 진입(장 시작, 진입가=전일 종가)
        if not holding and want == 1.0 and not block:
            holding, entry = True, c_prev
            r -= buy_cost
            trades.append((t, "BUY", c_prev))

        # 2) 보유 중이면 당일 손절/익절 또는 종가까지 보유
        if holding:
            stop_price = entry * (1 - stop_loss) if stop_loss > 0 else None
            take_price = entry * (1 + take_profit) if take_profit > 0 else None

            if stop_price is not None and low_k <= stop_price:
                exit_price = min(o, stop_price)        # 갭하락이면 시가 체결
                r += exit_price / c_prev - 1 - sell_cost
                holding, block = False, True
                trades.append((t, "STOP", exit_price))
            elif take_price is not None and h >= take_price:
                exit_price = max(o, take_price)        # 갭상승이면 시가 체결
                r += exit_price / c_prev - 1 - sell_cost
                holding, block = False, True
                trades.append((t, "TAKE", exit_price))
            else:
                r += c / c_prev - 1                    # 종가까지 보유
                if want == 0.0:                        # 전략이 청산 신호
                    r -= sell_cost
                    holding = False
                    trades.append((t, "SELL", c))

        rets.iloc[k] = r
        actual_pos.iloc[k] = 1.0 if holding else 0.0

    equity = initial_cash * (1.0 + rets).cumprod()
    trades_df = (
        pd.DataFrame(trades, columns=["date", "type", "price"]).set_index("date")
        if trades else pd.DataFrame()
    )
    return BacktestResult(
        equity=equity,
        returns=rets,
        position=actual_pos,
        trades=trades_df,
        initial_cash=initial_cash,
    )
