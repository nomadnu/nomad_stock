"""워크포워드 최적화 (walk-forward analysis).

시간을 굴려가며 반복:
  [학습구간]에서 최적 파라미터 탐색 → 바로 다음 [검증구간]에 적용 →
  한 칸 전진 → 다시 학습 → 적용 ...
각 검증구간의 수익률(out-of-sample)을 이어붙이면, "매 시점 그때까지의
데이터로만 최적화해 거래했을 때"의 현실적인 수익곡선이 된다.

단순 인/아웃샘플 1회 분할보다 강하게 과최적화를 걸러낸다. 파라미터가 시간이
지나도 안정적으로 좋은지(매 폴드에서 비슷한 값이 뽑히는지)도 볼 수 있다.
"""
from __future__ import annotations

import pandas as pd

from ..backtest.engine import run_backtest
from ..backtest.metrics import metrics_from_returns
from ..strategy import make_strategy
from .grid import grid_search


def walk_forward(
    df: pd.DataFrame,
    strategy_name: str,
    param_grid: dict[str, list],
    metric: str = "sharpe",
    train_size: int = 400,
    test_size: int = 120,
    warmup: int = 120,
) -> dict:
    """롤링 워크포워드.

    train_size : 각 폴드의 학습 일수
    test_size  : 각 폴드의 검증(실전적용) 일수 = 전진 보폭
    warmup     : 지표 워밍업용으로 검증구간 앞에 덧붙이는 일수(수익엔 미반영)
    """
    keys = list(param_grid.keys())
    folds = []
    oos_parts: list[pd.Series] = []

    i = train_size
    n = len(df)
    while i + test_size <= n:
        train = df.iloc[i - train_size : i]
        test_start_label = df.index[i]
        test_block = df.iloc[max(0, i - warmup) : i + test_size]  # 워밍업+검증

        table = grid_search(train, strategy_name, param_grid, metric)
        if table.empty:
            break
        best = {
            k: (int(table.iloc[0][k]) if float(table.iloc[0][k]).is_integer()
                else float(table.iloc[0][k]))
            for k in keys
        }

        res = run_backtest(test_block, make_strategy(strategy_name, **best))
        # 검증구간(워밍업 제외)만 추출
        oos = res.returns[res.returns.index >= test_start_label]
        oos_parts.append(oos)

        folds.append(
            {
                "test_period": f"{oos.index[0].date()} ~ {oos.index[-1].date()}",
                "best_params": best,
                **metrics_from_returns(oos),
            }
        )
        i += test_size

    if not oos_parts:
        raise ValueError("데이터가 부족해 폴드를 만들 수 없습니다. 기간을 늘리세요.")

    combined = pd.concat(oos_parts)
    combined = combined[~combined.index.duplicated(keep="first")]
    overall = metrics_from_returns(combined)

    # 비교 기준: 같은 OOS 구간 Buy&Hold
    bh_close = df["Close"].reindex(combined.index)
    bh_ret = bh_close.pct_change().fillna(0.0)
    overall_bh = metrics_from_returns(bh_ret)

    return {
        "folds": folds,
        "oos_period": f"{combined.index[0].date()} ~ {combined.index[-1].date()}",
        "overall": overall,
        "buyhold": overall_bh,
        "equity": (1.0 + combined).cumprod(),
    }
