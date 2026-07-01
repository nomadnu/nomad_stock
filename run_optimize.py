"""파라미터 격자탐색 + 과최적화 점검 실행.

인샘플(앞 70%)에서 최적 파라미터를 찾고, 아웃샘플(뒤 30%)에서 검증한다.

사용법:
    python run_optimize.py 005930 sma     # 삼성전자, 이동평균 교차 최적화
    python run_optimize.py 000660 rsi     # SK하이닉스, RSI 최적화
    python run_optimize.py 005930 sma cagr  # 정렬 기준을 CAGR로
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.data.loader import load_ohlcv
from nomad_stock.optimize import optimize_and_validate

# 전략별 탐색 격자
_GRIDS = {
    "sma": {
        "fast": [5, 10, 15, 20, 30, 40],
        "slow": [30, 50, 60, 80, 100, 120],
    },
    "rsi": {
        "period": [7, 14, 21],
        "oversold": [20, 25, 30, 35],
        "exit_level": [50, 55, 60, 70],
    },
    "bb": {
        "period": [10, 20, 30, 40],
        "num_std": [1.5, 2.0, 2.5, 3.0],
    },
}


def _fmt(m: dict) -> str:
    return (
        f"수익률 {m['total_return']*100:7.2f}% | CAGR {m['cagr']*100:6.2f}% | "
        f"MDD {m['mdd']*100:7.2f}% | Sharpe {m['sharpe']:5.2f} | 진입 {int(m['n_entries'])}"
    )


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"
    strat = sys.argv[2] if len(sys.argv) > 2 else "sma"
    metric = sys.argv[3] if len(sys.argv) > 3 else "sharpe"

    if strat not in _GRIDS:
        print(f"지원 전략: {', '.join(_GRIDS)}")
        return

    print(f"데이터 로딩: {symbol}")
    df = load_ohlcv(symbol, start="2018-01-01")
    print(f"  {len(df)}개 일봉\n")

    print(f"=== {symbol} / {strat} 최적화 (정렬기준: {metric}) ===")
    r = optimize_and_validate(df, strat, _GRIDS[strat], metric=metric)

    print(f"인샘플 기간 : {r['train_period']}")
    print(f"아웃샘플기간: {r['test_period']}\n")

    print("[인샘플 상위 5개 조합]")
    cols = list(_GRIDS[strat].keys()) + ["total_return", "cagr", "mdd", "sharpe", "n_entries"]
    print(r["train_table"][cols].head(5).to_string(index=False))

    print(f"\n[최적 파라미터] {r['best_params']}")
    print(f"  인샘플 : {_fmt(r['in_sample'])}")
    print(f"  아웃샘플: {_fmt(r['out_sample'])}")

    # 과최적화 간단 진단
    in_s, out_s = r["in_sample"]["sharpe"], r["out_sample"]["sharpe"]
    if in_s > 0 and out_s < in_s * 0.4:
        print("\n⚠️  아웃샘플 성과가 인샘플 대비 크게 하락 — 과최적화 의심.")
    elif out_s <= 0:
        print("\n⚠️  아웃샘플에서 음(-)의 성과 — 이 파라미터는 신뢰하기 어렵습니다.")
    else:
        print("\n✓ 아웃샘플에서도 성과 유지 — 상대적으로 견고한 편.")


if __name__ == "__main__":
    main()
