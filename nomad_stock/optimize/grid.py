"""파라미터 격자탐색 (grid search) + 과최적화 점검.

핵심 경고: 과거 '전체'에서 가장 좋은 파라미터를 고르면 그 구간에만 맞춘
값(curve fitting)일 수 있다. 그래서:
  1) 데이터를 인샘플(앞부분)/아웃샘플(뒷부분)으로 나눈다.
  2) 인샘플에서만 최적 파라미터를 찾는다.
  3) 그 파라미터를 '본 적 없는' 아웃샘플에 적용해 성과가 유지되는지 본다.
인샘플은 좋은데 아웃샘플에서 무너지면 과최적화 신호다.
"""
from __future__ import annotations

import itertools

import pandas as pd

from ..backtest.engine import run_backtest
from ..backtest.metrics import metrics_raw
from ..strategy import make_strategy


def walk_split(df: pd.DataFrame, train_ratio: float = 0.7):
    """시계열을 앞(train)/뒤(test)로 분할. 셔플하지 않는다(미래 누설 방지)."""
    n = int(len(df) * train_ratio)
    return df.iloc[:n], df.iloc[n:]


def grid_search(
    df: pd.DataFrame,
    strategy_name: str,
    param_grid: dict[str, list],
    metric: str = "sharpe",
) -> pd.DataFrame:
    """param_grid의 모든 조합을 백테스트하고 metric 내림차순으로 정렬해 반환.

    param_grid 예: {"fast": [5,10,20], "slow": [30,60,120]}
    잘못된 조합(예: SMA에서 fast>=slow)은 자동으로 건너뛴다.
    """
    keys = list(param_grid.keys())
    rows = []
    for combo in itertools.product(*(param_grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        try:
            strat = make_strategy(strategy_name, **params)
        except ValueError:
            continue  # 제약 위반 조합 스킵
        res = run_backtest(df, strat)
        m = metrics_raw(res)
        rows.append({**params, **m})

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values(metric, ascending=False).reset_index(drop=True)
    return out


def optimize_and_validate(
    df: pd.DataFrame,
    strategy_name: str,
    param_grid: dict[str, list],
    metric: str = "sharpe",
    train_ratio: float = 0.7,
) -> dict:
    """인샘플 최적화 → 아웃샘플 검증을 한 번에 수행.

    반환: {
      "train_table": 인샘플 전체 순위표,
      "best_params": 인샘플 1등 파라미터,
      "in_sample": 1등의 인샘플 지표,
      "out_sample": 1등 파라미터를 아웃샘플에 적용한 지표,
    }
    """
    train, test = walk_split(df, train_ratio)
    table = grid_search(train, strategy_name, param_grid, metric)
    if table.empty:
        raise ValueError("유효한 파라미터 조합이 없습니다.")

    param_keys = list(param_grid.keys())
    best_params = {k: table.iloc[0][k] for k in param_keys}
    # numpy 타입을 파이썬 기본형으로
    best_params = {k: (int(v) if float(v).is_integer() else float(v)) for k, v in best_params.items()}

    best_strat = make_strategy(strategy_name, **best_params)
    out_res = run_backtest(test, best_strat)

    return {
        "train_table": table,
        "best_params": best_params,
        "in_sample": {k: table.iloc[0][k] for k in ["total_return", "cagr", "mdd", "sharpe", "n_entries"]},
        "out_sample": metrics_raw(out_res),
        "train_period": f"{train.index[0].date()} ~ {train.index[-1].date()}",
        "test_period": f"{test.index[0].date()} ~ {test.index[-1].date()}",
    }
