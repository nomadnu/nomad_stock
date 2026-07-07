"""코스피 강세 종목 스캐너 (지침서 STEP 3~4).

12:50에 실행 → 자동필터 통과 종목 중 '오전장 강세' 후보를 찾아 판단정보와 함께 반환.

조건(지침서 v0.3, 모의투자로 검증하며 다듬을 예시안):
  자동필터: 시총 5,000억↑, 거래대금 50억↑, 동전주·우선주·스팩·ETN 제외
  강세조건: 당일 상승 + 종가가 당일 고가 근처(안 무너짐) + 60일선 위
            + 볼린저 상단 위 + 거래량 평소보다 많음
  정렬: PER 낮은 순 (적자·PER없음은 맨 뒤)

v1 한계(외부 데이터 필요 → 추후):
  - '강세 이유'(뉴스), 외국인·기관 수급, 사업 한줄 설명, 미국장 요약은 아직 미제공.
  - '오전 내내 유지'는 분봉 대신 '종가가 당일 고가 근처'로 근사.
"""
from __future__ import annotations

import FinanceDataReader as fdr
import pandas as pd

from . import rules
from .broker import KISClient
from .data.loader import load_ohlcv


def get_universe() -> pd.DataFrame:
    """자동필터를 통과한 코스피 종목 목록."""
    df = fdr.StockListing("KOSPI").dropna(subset=["Marcap", "Amount", "Close"])
    df = df[
        (df["Marcap"] >= rules.MIN_MARKET_CAP)
        & (df["Amount"] >= rules.MIN_TRADING_VALUE)
        & (df["Close"] >= 1000)  # 동전주 제외
    ]
    name = df["Name"].astype(str)
    bad = (
        name.str.contains("스팩|ETN|리츠", na=False)
        | name.str.endswith("우", na=False)
        | name.str.endswith("우B", na=False)
    )
    return df[~bad]


def _prescreen(df: pd.DataFrame, top: int = 15) -> pd.DataFrame:
    """목록 데이터만으로 '오늘 강세 + 안 무너짐' 1차 선별 (추가 조회 없이 빠르게)."""
    up = df[df["ChagesRatio"] > 0].copy()
    if up.empty:
        return up
    rng = (up["High"] - up["Low"]).replace(0, pd.NA)
    up["pos"] = (up["Close"] - up["Low"]) / rng     # 당일 저가~고가 중 위치
    up = up.dropna(subset=["pos"])
    strong = up[up["pos"] >= 0.6]                    # 당일 상단 60% 이상 유지
    return strong.sort_values("ChagesRatio", ascending=False).head(top)


def _passes_daily(code: str) -> tuple[bool, dict]:
    """일봉 기준 조건: 60일선 위 + 볼린저 상단 위 + 거래량 증가."""
    try:
        h = load_ohlcv(code, start="2024-06-01", use_cache=False)
    except Exception:
        return False, {}
    if len(h) < rules.MA_TREND + 5:
        return False, {}
    close, vol = h["Close"], h["Volume"]
    last = close.iloc[-1]
    ma60 = close.rolling(rules.MA_TREND).mean().iloc[-1]
    mid20 = close.rolling(20).mean().iloc[-1]  # 볼린저 중심선(강세권 기준, 완화)
    vol_ok = vol.iloc[-1] > vol.rolling(20).mean().iloc[-1]
    ok = bool(last > ma60 and last > mid20 and vol_ok)
    info = {
        "ret5": round((last / close.iloc[-6] - 1) * 100, 1) if len(close) > 6 else 0.0,
        "ret20": round((last / close.iloc[-21] - 1) * 100, 1) if len(close) > 21 else 0.0,
    }
    return ok, info


def _tier(marcap: float) -> str:
    if marcap >= 2_000_000_000_000:
        return "💎대형주"
    if marcap >= rules.MIN_MARKET_CAP:
        return "중형주"
    return "⚠️소형주"


def scan(client: KISClient | None = None, max_candidates: int = 3) -> list[dict]:
    """강세 후보를 판단정보와 함께 PER 낮은 순으로 반환 (최대 max_candidates개)."""
    client = client or KISClient()
    pres = _prescreen(get_universe())
    cands = []
    for _, row in pres.iterrows():
        code = str(row["Code"])
        ok, info = _passes_daily(code)
        if not ok:
            continue
        try:
            per = client.get_quote(code).get("per", 0.0)
        except Exception:
            per = 0.0
        cands.append(
            {
                "code": code,
                "name": str(row["Name"]),
                "price": int(row["Close"]),
                "change_pct": round(float(row["ChagesRatio"]), 2),
                "marcap": int(row["Marcap"]),
                "tier": _tier(row["Marcap"]),
                "per": per,
                "ret5": info.get("ret5", 0.0),
                "ret20": info.get("ret20", 0.0),
                # v1 미제공 (외부 데이터 필요)
                "reason": "정보 없음(기술적 강세)",
                "flow": "정보 없음",
                "business": "정보 없음",
            }
        )

    # PER 낮은 순 (적자·0·음수는 맨 뒤)
    cands.sort(key=lambda c: (c["per"] <= 0, c["per"] if c["per"] > 0 else float("inf")))
    return cands[:max_candidates]


# ===== 미국주식 (추천·정보만, KIS 연동 없음) =====
# 저녁(미국장 개장 전)에 '지난 미국장 마감 기준 강세 종목'을 추천.
# S&P500 대형주라 시총 필터 불필요. 데이터는 FDR(일봉)만 사용.
US_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "ADBE",
    "CRM", "AMD", "NFLX", "INTC", "QCOM", "CSCO", "TXN", "IBM", "JPM", "BAC",
    "WFC", "GS", "MS", "V", "MA", "AXP", "UNH", "JNJ", "LLY", "PFE",
    "MRK", "ABBV", "TMO", "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "KO",
    "PEP", "PG", "DIS", "XOM", "CVX", "BA", "CAT", "GE", "T", "VZ",
]

_US_NAMES: dict[str, tuple[str, str]] = {}  # symbol -> (name, sector) 캐시


def _us_meta(symbol: str) -> tuple[str, str]:
    """S&P500 목록에서 회사명·섹터를 가져온다(캐시)."""
    global _US_NAMES
    if not _US_NAMES:
        try:
            sp = fdr.StockListing("S&P500")
            _US_NAMES = {
                r["Symbol"]: (r["Name"], r.get("Sector", ""))
                for _, r in sp.iterrows()
            }
        except Exception:
            _US_NAMES = {}
    return _US_NAMES.get(symbol, (symbol, ""))


def scan_us(max_candidates: int = 5) -> list[dict]:
    """미국 대형주 강세 후보 (지난 마감 기준). 모멘텀 높은 순 정렬."""
    cands = []
    for sym in US_SYMBOLS:
        try:
            h = fdr.DataReader(sym, "2024-06-01")
        except Exception:
            continue
        if len(h) < rules.MA_TREND + 5:
            continue
        close, vol = h["Close"], h["Volume"]
        last = close.iloc[-1]
        ma60 = close.rolling(rules.MA_TREND).mean().iloc[-1]
        mid20 = close.rolling(20).mean().iloc[-1]  # 볼린저 중심선(강세권, 완화)
        vol_ok = vol.iloc[-1] > vol.rolling(20).mean().iloc[-1]
        if not (last > ma60 and last > mid20 and vol_ok):
            continue
        name, sector = _us_meta(sym)
        cands.append(
            {
                "symbol": sym,
                "name": name,
                "sector": sector,
                "price": round(float(last), 2),
                "change_pct": round((last / close.iloc[-2] - 1) * 100, 2),
                "ret5": round((last / close.iloc[-6] - 1) * 100, 1) if len(close) > 6 else 0.0,
                "ret20": round((last / close.iloc[-21] - 1) * 100, 1) if len(close) > 21 else 0.0,
            }
        )
    cands.sort(key=lambda c: c["ret20"], reverse=True)  # 1개월 모멘텀 높은 순
    return cands[:max_candidates]


def format_us_candidates(cands: list[dict]) -> str:
    """미국주식 추천 텍스트 (정보 제공용, 매수 버튼 없음)."""
    if not cands:
        return (
            "🇺🇸 지난 미국장 강세 조건을 통과한 대형주가 없습니다.\n"
            "→ 오늘은 미국장도 쉬어가는 게 좋아 보여요."
        )
    lines = ["🇺🇸 미국 대형주 강세 추천 (지난 마감 기준, 모멘텀순)"]
    for i, c in enumerate(cands, 1):
        lines.append(
            f"\n{i}. {c['name']} ({c['symbol']}) · {c['sector']}\n"
            f"   ${c['price']} ({c['change_pct']:+.1f}%) · "
            f"최근 5일 {c['ret5']:+.1f}% · 1개월 {c['ret20']:+.1f}%"
        )
    lines.append("\n\n※ 참고 정보예요. 매수는 직접 판단·주문하세요 (자동매매 아님).")
    return "\n".join(lines)


def format_candidates(cands: list[dict]) -> str:
    """승인 알림용 텍스트 (판단 보조 정보 포함)."""
    if not cands:
        return "📊 오늘 강세 조건을 통과한 종목이 없습니다.\n→ 안 사는 것도 정답입니다. 현금 대기."
    lines = [f"📊 오전장 강세 종목 {len(cands)}개 (PER 낮은 순, 코스피 대형주)"]
    for i, c in enumerate(cands, 1):
        per = f"PER {c['per']:.1f}" if c["per"] > 0 else "PER 없음(적자)"
        lines.append(
            f"\n{i}. {c['name']} ({c['code']}) {c['tier']}  {per}\n"
            f"   오늘 {c['change_pct']:+.1f}% · 현재가 {c['price']:,}\n"
            f"   최근 5일 {c['ret5']:+.1f}% · 1개월 {c['ret20']:+.1f}%\n"
            f"   강세이유: {c['reason']} · 수급: {c['flow']}"
        )
    return "\n".join(lines)
