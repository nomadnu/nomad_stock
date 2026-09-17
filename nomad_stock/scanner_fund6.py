"""펀더멘털 6트랙 스캐너 — 3박자(재무 관문 + 성장·밸류 점수화), 필터강도 3단계.

- 재무건전성 = 필수 관문(ROE·부채·흑자). 통과 못 하면 탈락.
- 성장성·밸류에이션 = 점수화(합산 상위 target_n 편입).
- 강도차: 엄선(관문 엄격·소수) / 중간 / 폭넓게(관문 느슨·다수).
- 미국=yfinance, 한국=KIS 재무비율. 임계값은 예시안(페이퍼로 조정).
"""
from __future__ import annotations

from .broker import KISClient
from .scanner_kr_fund import KR_FUND_UNIVERSE
from .tracks import TRACKS, is_us

# 미국 후보군 (v1.4): 성장주 + '꾸준한 우량 복리주'를 섞어 '차분한 우량성장' 취지에 맞춤.
# 3박자(재무 관문 + 성장·밸류 점수)가 다양한 성격의 종목을 고를 수 있게 넓게.
FUND6_US_UNIVERSE = [
    # 성장/기술
    "NVDA", "AMD", "AVGO", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NFLX", "CRM",
    "ADBE", "NOW", "INTU", "PANW", "CRWD", "SNPS", "CDNS", "KLAC", "AMAT", "MU",
    "QCOM", "ORCL", "UBER", "BKNG", "MELI", "ANET", "ISRG", "REGN", "VRTX", "MPWR",
    # 꾸준한 우량 복리주 (배당·방어·필수소비·헬스케어·금융인프라)
    "V", "MA", "COST", "UNH", "LLY", "PG", "KO", "PEP", "HD", "MCD",
    "ABBV", "TMO", "ADP", "ACN", "TXN", "HON", "CAT", "SPGI", "MCO", "ICE",
    "TJX", "LOW", "SYK", "ZTS", "ELV", "PGR", "MSI", "ITW", "ADI", "MMC",
]

# 성장 상한: '우량성장주'(초고성장 아님) 취지 — 매출성장 우선 + 상한.
# 25% 이상은 다 25로 봐서 초고성장에 과한 가점을 주지 않음(밸류가 제대로 작동).
GROWTH_CAP = 25.0

# 강도별 파라미터(예시안) — 재무 관문 엄격도 + 편입 종목 수
STRENGTH = {
    "strict": {"target_n": 3, "roe_min": 15.0, "debt_max": 100.0, "per_max": 40.0},
    "mid":    {"target_n": 5, "roe_min": 10.0, "debt_max": 150.0, "per_max": 55.0},
    "loose":  {"target_n": 8, "roe_min": 8.0,  "debt_max": 200.0, "per_max": 80.0},
}


def _value_score_us(peg, per) -> float:
    """성장 감안 합리적 가격(GARP). PEG<1.5 가점·>1.5 감점(초고평가 배제). PEG 없으면 PER."""
    if peg and peg > 0:
        return max(-20.0, min(20.0, (1.5 - peg) * 20))
    if per and per > 0:
        return max(-15.0, min(15.0, 25 - per))
    return 0.0


def _value_score_kr(per) -> float:
    """한국 PER 기준(평균 낮음). 12 근처를 적정으로, 비싸면 감점."""
    return max(-15.0, min(15.0, 12 - per)) if per and per > 0 else 0.0


def _raw_us() -> list[dict]:
    """미국 후보 원자료(강도 관문 전 지표)를 1회 수집. 흑자 관문은 공통 적용."""
    import yfinance as yf

    from .scanner import _us_meta
    raws = []
    for sym in FUND6_US_UNIVERSE:
        try:
            info = yf.Ticker(sym).info
        except Exception:
            continue
        roe = info.get("returnOnEquity")           # 0.15 = 15%
        if roe is None:
            continue
        ocf = info.get("operatingCashflow") or info.get("freeCashflow")
        if ocf is not None and ocf <= 0:           # 영업현금흐름 흑자(공통 관문)
            continue
        g = info.get("revenueGrowth"); eg = info.get("earningsGrowth")
        rawg = g if g is not None else eg          # 매출성장 우선(이익성장은 폭발값 잦음)
        growth = min((rawg or 0) * 100, GROWTH_CAP)
        peg = info.get("trailingPegRatio") or info.get("pegRatio")
        per = info.get("trailingPE")
        name, _ = _us_meta(sym)
        raws.append({
            "symbol": sym, "name": name or info.get("shortName", sym),
            "price": round(info.get("currentPrice") or info.get("regularMarketPrice") or 0, 2),
            "roe": roe * 100, "debt": info.get("debtToEquity"), "per": per,
            "growth": round(growth, 1), "score": round(growth + _value_score_us(peg, per), 1),
        })
    return raws


def _raw_kr(client: KISClient) -> list[dict]:
    """한국 후보 원자료(KIS 재무비율). ROE>0을 흑자 프록시(현금흐름 미제공)."""
    raws = []
    for code, name in KR_FUND_UNIVERSE.items():
        fr = client.financial_ratio(code)
        if not fr:
            continue
        roe = fr.get("roe")
        if roe is None or roe <= 0:
            continue
        try:
            q = client.get_quote(code)
            per, price = q.get("per", 0.0), q.get("price", 0)
        except Exception:
            per, price = None, 0
        rawg = fr.get("rev_growth")
        if rawg is None:
            rawg = fr.get("op_growth")
        growth = min((rawg or 0), GROWTH_CAP)
        raws.append({
            "symbol": code, "name": name, "price": price,
            "roe": roe, "debt": fr.get("debt_ratio"), "per": per,
            "growth": round(growth, 1), "score": round(growth + _value_score_kr(per), 1),
        })
    return raws


def _apply_strength(raws: list[dict], strength: str) -> list[dict]:
    """강도 관문(ROE·부채·PER 상한)으로 거른 뒤 점수 상위 target_n."""
    p = STRENGTH[strength]
    sel = []
    for c in raws:
        if c["roe"] < p["roe_min"]:
            continue
        if c["debt"] is not None and c["debt"] > p["debt_max"]:
            continue
        if c["per"] and c["per"] > p["per_max"]:
            continue
        d = dict(c)
        d["roe"] = round(c["roe"], 1)
        d["debt"] = round(c["debt"], 0) if c["debt"] is not None else None
        d["per"] = round(c["per"], 1) if c["per"] else None
        sel.append(d)
    sel.sort(key=lambda c: c["score"], reverse=True)
    return sel[:p["target_n"]]


def scan_us_fund(strength: str) -> list[dict]:
    return _apply_strength(_raw_us(), strength)


def scan_kr_fund6(client: KISClient, strength: str) -> list[dict]:
    return _apply_strength(_raw_kr(client), strength)


def scan_track(tid: str, client: KISClient | None = None) -> list[dict]:
    """트랙 id로 스캔 디스패치."""
    if is_us(tid):
        return scan_us_fund(TRACKS[tid]["strength"])
    return scan_kr_fund6(client or KISClient(), TRACKS[tid]["strength"])


def scan_market(market: str, client: KISClient | None = None) -> dict:
    """시장(US/KR)의 3강도 후보를 데이터 1회 조회로 반환 → {tid: [cands]}."""
    raws = _raw_us() if market == "US" else _raw_kr(client or KISClient())
    return {tid: _apply_strength(raws, v["strength"])
            for tid, v in TRACKS.items() if v["market"] == market}


def format_candidates(tid: str, cands: list[dict], affordable: set | None = None) -> str:
    """트랙명 머리표 + 후보 표시. affordable=1주 살 수 있는 심볼(없으면 표시 안 함)."""
    from .tracks import STRENGTH_KO, tag
    if not cands:
        return f"{tag(tid)} 3박자 통과 종목 없음 — 억지 편입 안 함(현금 대기)."
    us = is_us(tid)
    unit = "$" if us else "원"
    lines = [f"{tag(tid)} 편입 후보 {len(cands)}종목 (재무통과·점수순)"]
    for i, c in enumerate(cands, 1):
        per = f"PER {c['per']}" if c["per"] else "PER-"
        debt = f"부채 {c['debt']:.0f}%" if c["debt"] is not None else "부채-"
        aff = "" if affordable is None or c["symbol"] in affordable else " ⚠️현금부족(제외)"
        lines.append(
            f"\n{i}. {c['name']} ({c['symbol']}) {c['price']:,}{unit}{aff}\n"
            f"   성장 {c['growth']}% · ROE {c['roe']}% · {debt} · {per} · 점수 {c['score']}"
        )
    return "\n".join(lines)
