"""백테스트 실행 예제.

사용법:
    python run_backtest.py                      # 삼성전자, SMA 20x60
    python run_backtest.py 000660 10 30         # SK하이닉스, SMA 10x30
    python run_backtest.py AAPL 20 60 2020-01-01
"""
from __future__ import annotations

import sys

# Windows 콘솔에서 한글이 깨지지 않도록 UTF-8 출력 강제
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from nomad_stock.backtest import print_summary, run_backtest
from nomad_stock.data.loader import load_ohlcv
from nomad_stock.strategy import SmaCrossStrategy
from nomad_stock.strategy.base import Strategy


class BuyHold(Strategy):
    """비교 기준: 첫날 사서 끝까지 보유."""

    name = "BuyHold"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        s = pd.Series(1.0, index=df.index, name="target_position")
        return s


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"  # 삼성전자
    fast = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    slow = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    start = sys.argv[4] if len(sys.argv) > 4 else "2018-01-01"

    print(f"데이터 로딩: {symbol} ({start} ~)")
    df = load_ohlcv(symbol, start=start)
    print(f"  {len(df)}개 일봉 로드 완료")

    # 1) 전략 성과
    result = run_backtest(df, SmaCrossStrategy(fast=fast, slow=slow))
    print_summary(result, title=f"{symbol} SMA{fast}x{slow}")

    # 2) 비교 기준: 단순 매수후보유
    bh_result = run_backtest(df, BuyHold())
    print_summary(bh_result, title=f"{symbol} Buy&Hold")


if __name__ == "__main__":
    main()
