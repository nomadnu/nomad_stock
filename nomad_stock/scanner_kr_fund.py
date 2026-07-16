"""트랙 D(신규): 한국 펀더멘털 3박자 필터 (코스피200 대형주).

트랙 C(미국)의 한국판. 재무데이터는 한투(KIS) 재무비율 TR로 조회.
3박자: 성장성(매출·영업이익 증가율) · 재무건전성(ROE·부채비율) · 밸류(PER).
임계값은 예시안(페이퍼로 검증하며 조정). 셋 다 통과가 드물면 0종목 → 억지 편입 안 함.
"""
from __future__ import annotations

from .broker import KISClient

# 코스피200 대형주 후보군 (대표성·재무 양호 지향). 한투 재무 TR 호출 수를 묶기 위해 큐레이션.
KR_FUND_UNIVERSE: dict[str, str] = {
    "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스", "005380": "현대차", "000270": "기아",
    "068270": "셀트리온", "005490": "POSCO홀딩스", "035420": "NAVER",
    "035720": "카카오", "051910": "LG화학", "006400": "삼성SDI",
    "028260": "삼성물산", "012330": "현대모비스", "066570": "LG전자",
    "003670": "포스코퓨처엠", "034730": "SK", "017670": "SK텔레콤",
    "018260": "삼성에스디에스", "009150": "삼성전기", "010130": "고려아연",
    "033780": "KT&G", "010950": "S-Oil", "047810": "한국항공우주",
    "042700": "한미반도체", "011070": "LG이노텍", "247540": "에코프로비엠",
    "323410": "카카오뱅크", "086790": "하나금융지주", "105560": "KB금융",
    "055550": "신한지주", "032830": "삼성생명", "000810": "삼성화재",
}

# 3박자 임계값 (예시안, % 단위 — KIS 재무비율은 %로 옴). 페이퍼로 검증하며 조정.
MIN_GROWTH = 10.0    # 성장: 매출 또는 영업이익 증가율 10%↑
MIN_ROE = 8.0        # 재무: ROE 8%↑
MAX_DEBT = 150.0     # 재무: 부채비율 150% 미만
MAX_PER = 20.0       # 밸류: 0 < PER < 20 (성장 감안 합리적 가격)


def pass_3factor_kr(fr: dict | None, per: float | None) -> tuple[bool, dict]:
    """한국 3박자 판정. fr=KISClient.financial_ratio() 결과, per=현재 PER."""
    if not fr:
        return False, {}
    g, og = fr.get("rev_growth"), fr.get("op_growth")
    roe, debt = fr.get("roe"), fr.get("debt_ratio")
    growth = (g is not None and g >= MIN_GROWTH) or (og is not None and og >= MIN_GROWTH)
    financial = (roe is not None and roe >= MIN_ROE) and (debt is None or debt < MAX_DEBT)
    valuation = per is not None and 0 < per < MAX_PER
    m = {"rev_growth": g, "op_growth": og, "roe": roe, "debt_ratio": debt, "per": per,
         "yymm": fr.get("yymm", ""), "g_ok": growth, "f_ok": financial, "v_ok": valuation}
    return (growth and financial and valuation), m


def scan_kr_fund(client: KISClient | None = None, max_candidates: int = 5,
                 exclude: set | None = None) -> list[dict]:
    """3박자 통과 한국 편입 후보. 매출성장 높은 순. exclude=이미 보유(중복 편입 방지)."""
    client = client or KISClient()
    exclude = exclude or set()
    cands = []
    for code, name in KR_FUND_UNIVERSE.items():
        if code in exclude:
            continue
        fr = client.financial_ratio(code)
        if not fr:
            continue
        try:
            q = client.get_quote(code)
            per, price = q.get("per", 0.0), q.get("price", 0)
        except Exception:
            per, price = None, 0
        ok, m = pass_3factor_kr(fr, per)
        if not ok:
            continue
        cands.append({"code": code, "name": name, "price": price, **m})
    cands.sort(key=lambda c: (c["rev_growth"] or 0), reverse=True)
    return cands[:max_candidates]


def format_kr_fund_candidates(cands: list[dict]) -> str:
    if not cands:
        return ("📘 3박자(성장·재무·밸류) 모두 통과한 한국 종목이 없습니다.\n"
                "→ 억지로 편입하지 않아요. 현금 대기 (또는 재무 조회 불가 상태).")
    lines = ["📘 한국 펀더멘털 편입 후보 (3박자 통과, 성장순)"]
    for i, c in enumerate(cands, 1):
        g = f"{c['rev_growth']:.0f}%" if c["rev_growth"] is not None else "-"
        roe = f"{c['roe']:.0f}%" if c["roe"] is not None else "-"
        debt = f"{c['debt_ratio']:.0f}%" if c["debt_ratio"] is not None else "-"
        per = f"{c['per']:.1f}" if c["per"] else "-"
        lines.append(
            f"\n{i}. {c['name']} ({c['code']})  {c['price']:,}원\n"
            f"   성장 매출 {g} ✅ · ROE {roe}·부채 {debt} ✅ · PER {per} ✅"
        )
    lines.append("\n\n[편입 승인] 누른 종목만 한국 펀더멘털 장부에 기록돼요 (5종목 이내 집중).")
    return "\n".join(lines)
