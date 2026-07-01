"""워크포워드 최적화 실행.

매 폴드에서 학습→검증을 굴려가며, 검증구간 수익을 이어붙인 '현실적' 성과를 낸다.
단순 1회 인/아웃샘플(run_optimize.py)보다 과최적화를 강하게 거른다.

사용법:
    python run_walkforward.py 005930 sma     # 삼성전자, 이동평균
    python run_walkforward.py 000660 rsi     # SK하이닉스, RSI
    python run_walkforward.py 005930 bb cagr # 볼린저, 정렬기준 CAGR
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.backtest import plot_equity
from nomad_stock.data.loader import load_ohlcv
from nomad_stock.optimize import walk_forward

# run_optimize.py와 동일한 격자 재사용
_GRIDS = {
    "sma": {"fast": [5, 10, 15, 20, 30, 40], "slow": [30, 50, 60, 80, 100, 120]},
    "rsi": {"period": [7, 14, 21], "oversold": [20, 25, 30, 35], "exit_level": [50, 55, 60, 70]},
    "bb": {"period": [10, 20, 30, 40], "num_std": [1.5, 2.0, 2.5, 3.0]},
}


def _fmt(m: dict) -> str:
    return (
        f"수익률 {m['total_return']*100:7.2f}% | CAGR {m['cagr']*100:6.2f}% | "
        f"MDD {m['mdd']*100:7.2f}% | Sharpe {m['sharpe']:5.2f}"
    )


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_plot = "--plot" in sys.argv
    symbol = args[0] if len(args) > 0 else "005930"
    strat = args[1] if len(args) > 1 else "sma"
    metric = args[2] if len(args) > 2 else "sharpe"

    if strat not in _GRIDS:
        print(f"지원 전략: {', '.join(_GRIDS)}")
        return

    print(f"데이터 로딩: {symbol}")
    df = load_ohlcv(symbol, start="2018-01-01")
    print(f"  {len(df)}개 일봉\n")

    print(f"=== {symbol} / {strat} 워크포워드 (정렬기준 {metric}) ===")
    r = walk_forward(df, strat, _GRIDS[strat], metric=metric)

    print(f"\n[폴드별 선택 파라미터 & 검증성과] (총 {len(r['folds'])}개 폴드)")
    for i, f in enumerate(r["folds"], 1):
        print(f"  {i:2d}. {f['test_period']} | {f['best_params']}")
        print(f"      {_fmt(f)}")

    print(f"\n[전체 OOS 결합] {r['oos_period']}")
    print(f"  워크포워드 : {_fmt(r['overall'])}")
    print(f"  Buy&Hold   : {_fmt(r['buyhold'])}")

    wf, bh = r["overall"], r["buyhold"]
    if wf["sharpe"] <= 0:
        print("\n⚠️  결합 OOS 성과가 음(-) — 이 전략/종목 조합은 실전에 부적합.")
    elif wf["sharpe"] >= bh["sharpe"]:
        print("\n✓ 워크포워드 성과가 단순보유 이상 — 전략에 실효성 있음.")
    else:
        print("\n△ 양(+)이지만 단순보유보다 약함 — 종목/전략 재검토 권장.")

    if do_plot:
        import pandas as pd

        wf_eq = r["equity"] * 10_000_000
        bh_eq = (1.0 + df["Close"].reindex(wf_eq.index).pct_change().fillna(0.0)).cumprod() * 10_000_000
        path = plot_equity(
            {"워크포워드": wf_eq, "Buy&Hold": bh_eq},
            f"{symbol} {strat} 워크포워드 OOS",
            f"charts/{symbol}_{strat}_walkforward.png",
            drawdown_name="워크포워드",
        )
        print(f"\n[그래프 저장] {path}")


if __name__ == "__main__":
    main()
