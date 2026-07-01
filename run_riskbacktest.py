"""손절/익절을 적용한 백테스트 비교.

같은 전략을 ① 리스크 없음 ② 손절/익절 적용 으로 백테스트해, 손절/익절이
수익·낙폭·거래수에 어떤 영향을 주는지 비교하고 그래프로 저장한다.

사용법:
    python run_riskbacktest.py                       # 삼성, SMA20x60, 손절8%/익절20%
    python run_riskbacktest.py 000660 10 30 0.05 0.15
    python run_riskbacktest.py 005930 20 60 0.08 0.20 --plot
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.backtest import plot_equity, run_backtest, summarize
from nomad_stock.backtest.risk_engine import run_backtest_with_risk
from nomad_stock.data.loader import load_ohlcv
from nomad_stock.strategy import SmaCrossStrategy


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_plot = "--plot" in sys.argv

    symbol = args[0] if len(args) > 0 else "005930"
    fast = int(args[1]) if len(args) > 1 else 20
    slow = int(args[2]) if len(args) > 2 else 60
    stop = float(args[3]) if len(args) > 3 else 0.08
    take = float(args[4]) if len(args) > 4 else 0.20

    print(f"데이터 로딩: {symbol}")
    df = load_ohlcv(symbol, start="2018-01-01")
    print(f"  {len(df)}개 일봉\n")

    strat = SmaCrossStrategy(fast, slow)
    base = run_backtest(df, strat)
    risk = run_backtest_with_risk(df, SmaCrossStrategy(fast, slow), stop_loss=stop, take_profit=take)

    bs, rs = summarize(base), summarize(risk)
    print(f"=== {symbol} SMA{fast}x{slow} | 손절 {stop*100:.0f}% / 익절 {take*100:.0f}% ===")
    cols = ["총수익률", "CAGR(연복리)", "MDD(최대낙폭)", "Sharpe", "거래횟수(진입)"]
    print(f"{'':<14}{'리스크없음':>14}{'손절/익절적용':>16}")
    for c in cols:
        print(f"{c:<14}{str(bs.get(c,'')):>14}{str(rs.get(c,'')):>16}")

    # 청산 유형 분포
    if not risk.trades.empty and "type" in risk.trades.columns:
        vc = risk.trades["type"].value_counts().to_dict()
        print(f"\n청산 내역: 손절 {vc.get('STOP',0)}회 / 익절 {vc.get('TAKE',0)}회 / 신호청산 {vc.get('SELL',0)}회")

    if do_plot:
        path = plot_equity(
            {"리스크없음": base.equity, "손절/익절": risk.equity},
            f"{symbol} SMA{fast}x{slow} 손절{stop*100:.0f}%/익절{take*100:.0f}%",
            f"charts/{symbol}_risk.png",
            drawdown_name="손절/익절",
        )
        print(f"\n[그래프 저장] {path}")


if __name__ == "__main__":
    main()
