"""트랙 C: 장기투자 3박자 필터 (성장성·재무건전성·밸류에이션).

미국 재무지표는 yfinance로 조회. 세 박자를 모두 통과한 종목만 편입 후보.
지침서 경고: 셋 다 통과는 드물다 → 필터가 빡세면 0종목. 기준은 '상대적 우수'로 두고 조정.
임계값은 검증하며 다듬는 예시안.
"""
from __future__ import annotations

import yfinance as yf

from .scanner import _us_meta

# 성장주+우량주 후보군 (S&P500 내, 텐베거 잠재력·재무 양호 지향). 확장 가능.
LONG_UNIVERSE = [
    "NVDA", "AMD", "AVGO", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NFLX",
    "CRM", "ADBE", "NOW", "INTU", "PANW", "CRWD", "FTNT", "SNPS", "CDNS", "LRCX",
    "KLAC", "AMAT", "MU", "QCOM", "ORCL", "UBER", "ABNB", "BKNG", "MELI", "AXON",
    "MPWR", "ANET", "DXCM", "ISRG", "REGN", "VRTX", "LLY", "NOW", "TEAM", "WDAY",
]

# 3박자 임계값 (예시안, 조정 가능)
MIN_REV_GROWTH = 0.15      # 성장: 매출성장률 15%↑
MIN_ROE = 0.12            # 재무: ROE 12%↑
MAX_DEBT_EQUITY = 150.0   # 재무: 부채비율(D/E, %) 150 미만
MAX_PEG = 2.5            # 밸류: PEG 2.5 미만(성장 감안 합리성)


def pass_3factor(info: dict) -> tuple[bool, dict]:
    """성장성·재무건전성·밸류에이션 3박자 판정."""
    g = info.get("revenueGrowth")
    eg = info.get("earningsGrowth")
    roe = info.get("returnOnEquity")
    dte = info.get("debtToEquity")
    fcf = info.get("freeCashflow") or info.get("operatingCashflow")
    peg = info.get("trailingPegRatio") or info.get("pegRatio")

    growth = (g is not None and g >= MIN_REV_GROWTH) or (eg is not None and eg >= MIN_REV_GROWTH)
    financial = (
        (roe is not None and roe >= MIN_ROE)
        and (dte is None or dte < MAX_DEBT_EQUITY)
        and (fcf is None or fcf > 0)
    )
    valuation = peg is not None and 0 < peg < MAX_PEG
    metrics = {
        "rev_growth": g, "roe": roe, "debt_equity": dte, "peg": peg,
        "pe": info.get("trailingPE"),
        "g_ok": growth, "f_ok": financial, "v_ok": valuation,
    }
    return (growth and financial and valuation), metrics


def scan_long(max_candidates: int = 5) -> list[dict]:
    """3박자 통과 장기 편입 후보. 성장률 높은 순."""
    cands = []
    for sym in LONG_UNIVERSE:
        try:
            info = yf.Ticker(sym).info
        except Exception:
            continue
        ok, m = pass_3factor(info)
        if not ok:
            continue
        name, sector = _us_meta(sym)
        cands.append({
            "symbol": sym, "name": name or info.get("shortName", sym),
            "sector": sector or info.get("sector", ""),
            "price": round(info.get("currentPrice") or info.get("regularMarketPrice") or 0, 2),
            **m,
        })
    cands.sort(key=lambda c: (c["rev_growth"] or 0), reverse=True)
    return cands[:max_candidates]


def format_long_candidates(cands: list[dict]) -> str:
    """장기 편입 후보 텍스트 (3박자 지표 표시)."""
    if not cands:
        return (
            "📗 3박자(성장·재무·밸류) 모두 통과한 종목이 없습니다.\n"
            "→ 억지로 편입하지 않아요. 현금 보유가 정답 (지침서 원칙)."
        )
    lines = ["📗 장기 편입 후보 (3박자 통과, 성장률순)"]
    for i, c in enumerate(cands, 1):
        rg = f"{c['rev_growth']*100:.0f}%" if c["rev_growth"] is not None else "-"
        roe = f"{c['roe']*100:.0f}%" if c["roe"] is not None else "-"
        peg = f"{c['peg']:.1f}" if c["peg"] is not None else "-"
        de = f"{c['debt_equity']:.0f}" if c["debt_equity"] is not None else "-"
        lines.append(
            f"\n{i}. {c['name']} ({c['symbol']}) · {c['sector']}  ${c['price']}\n"
            f"   성장 매출 {rg} ✅ · 재무 ROE {roe}·부채 {de} ✅ · 밸류 PEG {peg} ✅"
        )
    lines.append("\n\n[편입 승인] 누른 종목만 장기 장부에 기록돼요 (5종목 이내 집중).")
    return "\n".join(lines)
