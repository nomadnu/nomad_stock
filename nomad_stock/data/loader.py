"""주가 데이터 로더.

FinanceDataReader로 국내/해외 일봉 데이터를 받아오고, 로컬 CSV에 캐싱한다.
백테스트는 실거래 API(KIS)와 분리되어 있어서, 데이터 소스만 무료인 FDR을 쓴다.
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd

try:
    import FinanceDataReader as fdr
except ImportError:  # 설치 전이라도 모듈 import 자체는 되도록
    fdr = None

# 캐시 디렉터리 (프로젝트 루트의 data_cache/)
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data_cache"
)


def _cache_path(symbol: str, start: str, end: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    safe = symbol.replace("/", "_")
    return os.path.join(_CACHE_DIR, f"{safe}_{start}_{end}.csv")


def load_ohlcv(
    symbol: str,
    start: str = "2018-01-01",
    end: str | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """일봉 OHLCV 데이터를 DataFrame으로 반환한다.

    Parameters
    ----------
    symbol : 종목코드. 국내는 "005930"(삼성전자), 해외는 "AAPL" 등.
    start, end : "YYYY-MM-DD". end가 None이면 오늘.
    use_cache : True면 같은 (symbol, start, end) 요청을 CSV로 캐싱/재사용.

    Returns
    -------
    DataFrame (index=DatetimeIndex, columns=[Open, High, Low, Close, Volume])
    """
    if end is None:
        end = date.today().isoformat()

    path = _cache_path(symbol, start, end)
    if use_cache and os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return _clean(df)

    if fdr is None:
        raise ImportError(
            "FinanceDataReader가 설치되지 않았습니다. `pip install finance-datareader`"
        )

    df = fdr.DataReader(symbol, start, end)
    # FDR은 보통 Open/High/Low/Close/Volume 컬럼을 준다. Change 컬럼은 버린다.
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].dropna()
    df.index.name = "Date"
    df = _clean(df)

    if use_cache:
        df.to_csv(path)
    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """불량 행 제거: 가격(OHLC)이 0 이하인 거래정지/결손일 등을 버린다."""
    price_cols = [c for c in ["Open", "High", "Low", "Close"] if c in df.columns]
    if price_cols:
        mask = (df[price_cols] > 0).all(axis=1)
        df = df[mask]
    return df
