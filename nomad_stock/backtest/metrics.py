"""백테스트 성과 지표 계산."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import BacktestResult

_TRADING_DAYS = 252  # 연환산 기준 거래일 수


def _cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    total_return = equity.iloc[-1] / equity.iloc[0]
    years = len(equity) / _TRADING_DAYS
    if years <= 0:
        return 0.0
    return total_return ** (1 / years) - 1


def _max_drawdown(equity: pd.Series) -> float:
    """최대 낙폭(MDD). 음수 비율로 반환 (예: -0.32 = -32%)."""
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def _sharpe(returns: pd.Series, rf: float = 0.0) -> float:
    excess = returns - rf / _TRADING_DAYS
    std = excess.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return float(np.sqrt(_TRADING_DAYS) * excess.mean() / std)


def metrics_from_returns(returns: pd.Series, initial_cash: float = 10_000_000) -> dict:
    """일별 수익률 시계열에서 지표를 직접 계산 (워크포워드 OOS 결합용)."""
    returns = returns.fillna(0.0)
    equity = initial_cash * (1.0 + returns).cumprod()
    if equity.empty:
        return {"total_return": 0.0, "cagr": 0.0, "mdd": 0.0, "sharpe": 0.0}
    return {
        "total_return": float(equity.iloc[-1] / initial_cash - 1.0),
        "cagr": _cagr(equity),
        "mdd": _max_drawdown(equity),
        "sharpe": _sharpe(returns),
    }


def metrics_raw(result: BacktestResult) -> dict:
    """정렬·비교용 숫자 지표 (포맷 없는 float). 최적화에서 사용."""
    eq = result.equity
    trades = result.trades
    if not trades.empty and "pos_change" in trades.columns:
        n_entries = int((trades["pos_change"] > 0).sum())
    else:
        n_entries = len(trades)
    return {
        "total_return": result.final_equity / result.initial_cash - 1.0,
        "cagr": _cagr(eq),
        "mdd": _max_drawdown(eq),
        "sharpe": _sharpe(result.returns),
        "n_entries": n_entries,
    }


def summarize(result: BacktestResult) -> dict:
    """결과를 사람이 읽기 쉬운 지표 dict로 요약한다."""
    eq = result.equity
    rets = result.returns

    trades = result.trades
    # 진입 횟수: 엔진별 거래내역 구조가 달라 분기.
    if not trades.empty and "pos_change" in trades.columns:
        n_entries = int((trades["pos_change"] > 0).sum())          # 기본 엔진
    elif not trades.empty and "type" in trades.columns:
        n_entries = int((trades["type"] == "BUY").sum())           # 리스크 엔진
    else:
        n_entries = len(trades)                                     # 돌파 엔진

    summary = {
        "기간": f"{eq.index[0].date()} ~ {eq.index[-1].date()}",
        "초기자본": round(result.initial_cash),
        "최종자산": round(result.final_equity),
        "총수익률": f"{(result.final_equity / result.initial_cash - 1) * 100:.2f}%",
        "CAGR(연복리)": f"{_cagr(eq) * 100:.2f}%",
        "MDD(최대낙폭)": f"{_max_drawdown(eq) * 100:.2f}%",
        "Sharpe": round(_sharpe(rets), 2),
        "거래횟수(진입)": n_entries,
    }
    # 비용 컬럼이 있는 엔진만 총비용 표시
    if not trades.empty and "cost" in trades.columns:
        summary["총비용"] = round(float(trades["cost"].sum() * result.initial_cash))
    return summary


def print_summary(result: BacktestResult, title: str = "백테스트 결과") -> None:
    print(f"\n===== {title} =====")
    for k, v in summarize(result).items():
        print(f"  {k:<14}: {v}")
    print("=" * (12 + len(title)))
