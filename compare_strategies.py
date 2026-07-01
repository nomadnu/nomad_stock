"""한 종목에 여러 전략을 백테스트해 성과를 비교한다.

사용법:
    python compare_strategies.py                 # 삼성전자
    python compare_strategies.py 000660 2020-01-01
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from nomad_stock.backtest import plot_equity, run_backtest, summarize
from nomad_stock.data.loader import load_ohlcv
from nomad_stock.strategy import BollingerStrategy, RsiStrategy, SmaCrossStrategy
from nomad_stock.strategy.base import Strategy
from nomad_stock.strategy.breakout import run_breakout_backtest


class BuyHold(Strategy):
    name = "BuyHold"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index, name="target_position")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_plot = "--plot" in sys.argv
    symbol = args[0] if len(args) > 0 else "005930"
    start = args[1] if len(args) > 1 else "2018-01-01"

    print(f"데이터 로딩: {symbol} ({start} ~)")
    df = load_ohlcv(symbol, start=start)
    print(f"  {len(df)}개 일봉\n")

    rows = []
    curves = {}

    # 종가기반 전략들은 공용 엔진으로
    for strat in [
        BuyHold(),
        SmaCrossStrategy(20, 60),
        SmaCrossStrategy(10, 30),
        RsiStrategy(14, 30, 50),
        BollingerStrategy(20, 2.0),
    ]:
        res = run_backtest(df, strat)
        rows.append((strat.name, summarize(res)))
        curves[strat.name] = res.equity

    # 변동성 돌파는 전용 엔진으로
    for k in (0.3, 0.5, 0.7):
        res = run_breakout_backtest(df, k=k)
        rows.append((f"Breakout(k={k})", summarize(res)))
        curves[f"Breakout(k={k})"] = res.equity

    # 표 출력
    cols = ["총수익률", "CAGR(연복리)", "MDD(최대낙폭)", "Sharpe", "거래횟수(진입)"]
    print(f"{'전략':<18}" + "".join(f"{c:>14}" for c in cols))
    print("-" * (18 + 14 * len(cols)))
    for name, s in rows:
        print(f"{name:<18}" + "".join(f"{str(s.get(c, '')):>14}" for c in cols))

    if do_plot:
        # 보기 좋게 대표 전략만 (돌파는 스케일이 달라 제외)
        sel = {n: curves[n] for n in ["BuyHold", "SMA20x60", "RSI14(30/50)", "BB20(2σ)"] if n in curves}
        path = plot_equity(sel, f"{symbol} 전략 비교", f"charts/{symbol}_compare.png", drawdown_name="SMA20x60")
        print(f"\n[그래프 저장] {path}")


if __name__ == "__main__":
    main()
