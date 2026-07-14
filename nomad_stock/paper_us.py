"""트랙 B: 미국주식 페이퍼 트레이딩 가상 장부 (지침서 v0.5 / 작업지시서 트랙B).

⚠️ 실제 주문 절대 없음. 100% 앱 내부 JSON 장부.
- 원금: 1,000만원을 기록시점 환율로 환전한 달러 (약 6,500불)
- 한 종목 한도: 원금의 20%(200만원)어치
- 손익: 달러·원화 양쪽 + 환율효과 분리
- 시세·환율: FinanceDataReader(무료)로 조회 (KIS 해외API 대체, 페이퍼라 충분)
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import FinanceDataReader as fdr

from . import rules

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 봇·대시보드 컨테이너가 공유하는 state 폴더(도커 볼륨). 장부를 양쪽이 함께 본다.
_STATE_DIR = os.path.join(_ROOT, "state")
os.makedirs(_STATE_DIR, exist_ok=True)
_LEDGER_PATH = os.path.join(_STATE_DIR, "paper_us.json")
_RESV_PATH = os.path.join(_STATE_DIR, "us_reservations.json")


# ----- 시세·환율 -----
def us_price(symbol: str) -> float:
    """미국 종목 현재가(달러). 장중이면 당일 실시간, 아니면 최근 종가."""
    df = fdr.DataReader(symbol, "2026-01-01")
    return round(float(df["Close"].iloc[-1]), 2)


def fx_rate() -> float:
    """USD/KRW 환율."""
    df = fdr.DataReader("USD/KRW", "2026-01-01")
    return round(float(df["Close"].iloc[-1]), 2)


def index_return(ticker: str, start: str):
    """지수 start~현재 수익률(%). 시작 구간 이상치(튀는 값) 완화: 앞 3개 종가 중앙값을 기준.

    한국 지수(FDR)는 가끔 하루짜리 튀는 값이 있어, 시작일 하나에 좌우되지 않도록
    앞부분 여러 종가의 중앙값을 베이스로 쓴다. 조회 실패/데이터 없음이면 None.
    """
    try:
        df = fdr.DataReader(ticker, start)
        closes = [float(x) for x in df["Close"].tolist() if x == x]  # NaN 제외
        if not closes:
            return None
        head = sorted(closes[: min(3, len(closes))])
        base = head[len(head) // 2]  # 중앙값
        if base <= 0:
            return None
        return (closes[-1] / base - 1) * 100
    except Exception:
        return None


def ledger_start(ledger: dict):
    """장부의 매매 시작일(가장 이른 거래·편입일 'YYYY-MM-DD'). 거래 없으면 None."""
    dates = []
    for h in ledger.get("history", []):
        t = h.get("t", "")
        if t:
            dates.append(t[:10])
    for pos in ledger.get("positions", {}).values():
        if pos.get("buy_date"):
            dates.append(pos["buy_date"])
    return min(dates) if dates else None


# ----- 장부 상태 -----
def load_ledger() -> dict:
    if os.path.exists(_LEDGER_PATH):
        try:
            with open(_LEDGER_PATH, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    # 최초 생성: 원금 1000만원을 현재 환율로 달러 환전
    fx = fx_rate()
    cash = round(rules.DEFAULT_CAPITAL / fx, 2)
    ledger = {
        "capital_krw": rules.DEFAULT_CAPITAL,
        "init_fx": fx,
        "cash_usd": cash,
        "positions": {},   # symbol -> {name, qty, avg_usd, buy_fx, buy_date}
        "history": [],     # 체결 기록
    }
    save_ledger(ledger)
    return ledger


def save_ledger(ledger: dict) -> None:
    with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def position_budget_usd(fx: float) -> float:
    """한 종목 한도(원금 20% = 200만원)를 달러로."""
    krw = min(rules.DEFAULT_CAPITAL * rules.MAX_POSITION_PCT, rules.MAX_POSITION_AMOUNT)
    return krw / fx


# ----- 매수/매도 기록 (가상) -----
def record_buy(symbol: str, name: str, price_usd: float, fx: float) -> dict:
    """가상 매수 기록. 한 종목 한도 내 수량. 반환: 결과 dict."""
    ledger = load_ledger()
    budget = position_budget_usd(fx)
    qty = int(budget // price_usd)
    if qty < 1:
        return {"ok": False, "msg": f"{symbol}: 한도(${budget:.0f})로 1주도 못 삽니다 (${price_usd})."}
    cost = round(qty * price_usd, 2)
    if cost > ledger["cash_usd"]:
        qty = int(ledger["cash_usd"] // price_usd)
        if qty < 1:
            return {"ok": False, "msg": f"{symbol}: 현금 부족 (${ledger['cash_usd']:.0f})."}
        cost = round(qty * price_usd, 2)

    pos = ledger["positions"].get(symbol)
    if pos:  # 평단 갱신
        total_qty = pos["qty"] + qty
        pos["avg_usd"] = round((pos["avg_usd"] * pos["qty"] + price_usd * qty) / total_qty, 2)
        pos["qty"] = total_qty
    else:
        ledger["positions"][symbol] = {
            "name": name, "qty": qty, "avg_usd": price_usd,
            "buy_fx": fx, "buy_date": datetime.now().strftime("%Y-%m-%d"),
        }
    ledger["cash_usd"] = round(ledger["cash_usd"] - cost, 2)
    ledger["history"].append(
        {"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "action": "BUY",
         "symbol": symbol, "qty": qty, "price": price_usd, "fx": fx}
    )
    save_ledger(ledger)
    return {"ok": True, "msg": f"📝 [페이퍼 매수] {name}({symbol}) {qty}주 @ ${price_usd} "
                               f"(환율 {fx:.0f}, ≈{qty*price_usd*fx:,.0f}원)"}


def record_sell(symbol: str, price_usd: float, fx: float, reason: str = "매도") -> dict:
    """가상 매도(전량) 기록. 실현손익 계산."""
    ledger = load_ledger()
    pos = ledger["positions"].get(symbol)
    if not pos:
        return {"ok": False, "msg": f"{symbol}: 보유하고 있지 않아요."}
    qty = pos["qty"]
    proceeds = round(qty * price_usd, 2)
    pnl_usd = round((price_usd - pos["avg_usd"]) * qty, 2)
    ledger["cash_usd"] = round(ledger["cash_usd"] + proceeds, 2)
    del ledger["positions"][symbol]
    ledger["history"].append(
        {"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "action": "SELL",
         "symbol": symbol, "qty": qty, "price": price_usd, "fx": fx,
         "pnl_usd": pnl_usd, "reason": reason}
    )
    save_ledger(ledger)
    pct = (price_usd / pos["avg_usd"] - 1) * 100 if pos["avg_usd"] else 0
    return {"ok": True, "msg": f"📝 [페이퍼 {reason}] {pos['name']}({symbol}) {qty}주 @ ${price_usd} "
                               f"(손익 ${pnl_usd:+,.0f}, {pct:+.1f}%)"}


# ----- 평가 -----
def evaluate() -> dict:
    """현재 시세·환율로 장부 평가. 달러·원화 손익 + 환율효과 분리."""
    ledger = load_ledger()
    fx = fx_rate()
    rows = []
    holdings_value_usd = 0.0
    for sym, pos in ledger["positions"].items():
        try:
            cur = us_price(sym)
        except Exception:
            cur = pos["avg_usd"]
        val_usd = cur * pos["qty"]
        holdings_value_usd += val_usd
        pnl_usd = (cur - pos["avg_usd"]) * pos["qty"]
        # 원화 손익 = 원화현재가치 - 원화매입원가
        krw_now = val_usd * fx
        krw_cost = pos["avg_usd"] * pos["qty"] * pos["buy_fx"]
        pnl_krw = krw_now - krw_cost
        # 환율효과 = 매입금액(달러)에 (현재환율-매입환율) 곱한 것
        fx_effect_krw = pos["avg_usd"] * pos["qty"] * (fx - pos["buy_fx"])
        rows.append({
            "symbol": sym, "name": pos["name"], "qty": pos["qty"],
            "avg_usd": pos["avg_usd"], "cur_usd": cur,
            "pct": (cur / pos["avg_usd"] - 1) * 100 if pos["avg_usd"] else 0,
            "pnl_usd": pnl_usd, "pnl_krw": pnl_krw, "fx_effect_krw": fx_effect_krw,
        })
    total_usd = ledger["cash_usd"] + holdings_value_usd
    return {
        "fx": fx, "cash_usd": ledger["cash_usd"],
        "holdings_value_usd": holdings_value_usd,
        "total_usd": total_usd, "total_krw": total_usd * fx,
        "capital_krw": ledger["capital_krw"],
        "pnl_krw": total_usd * fx - ledger["capital_krw"],
        "rows": rows,
    }


# ----- 예약 (밤 9시 승인 → 개장+3h20m 기록) -----
def add_reservation(action: str, symbol: str, name: str) -> None:
    """승인 시 예약 저장. action: 'buy' | 'sell'."""
    items = _load_resv()
    items = [x for x in items if not (x["symbol"] == symbol and x["action"] == action)]
    items.append({"action": action, "symbol": symbol, "name": name})
    with open(_RESV_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)


def _load_resv() -> list:
    if os.path.exists(_RESV_PATH):
        try:
            with open(_RESV_PATH, encoding="utf-8") as f:
                return json.load(f).get("items", [])
        except json.JSONDecodeError:
            pass
    return []


def pop_reservations() -> list:
    """예약 전체를 반환하고 비운다 (기록 시각에 처리)."""
    items = _load_resv()
    with open(_RESV_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": []}, f)
    return items


# ----- 보유 종목 매도 판정 (재평가·손절 근접) -----
def evaluate_us_holding(symbol: str, avg_usd: float) -> tuple[str, str]:
    """(상태, 사유). 손절선 근접·60일선 이탈 시 매도검토."""
    try:
        cur = us_price(symbol)
    except Exception:
        return "주의", "시세 조회 실패"
    pnl = (cur / avg_usd - 1) * 100 if avg_usd else 0
    if pnl <= -rules.STOP_LOSS_PCT * 100:
        return "매도검토", f"손절선(-{rules.STOP_LOSS_PCT*100:.0f}%) 도달 {pnl:+.1f}%"
    try:
        df = fdr.DataReader(symbol, "2024-06-01")
        ma60 = df["Close"].rolling(rules.MA_TREND).mean().iloc[-1]
        if df["Close"].iloc[-1] < ma60:
            return "매도검토", f"60일선 이탈 ({pnl:+.1f}%)"
    except Exception:
        pass
    if pnl <= -5:
        return "주의", f"손절 근접 {pnl:+.1f}%"
    return "보유권장", f"양호 {pnl:+.1f}%"


def format_balance() -> str:
    """/미국잔고 응답 텍스트 (달러·원화)."""
    e = evaluate()
    lines = [
        f"🇺🇸 미국 페이퍼 계좌 (환율 {e['fx']:.0f})",
        f"현금 ${e['cash_usd']:,.0f} · 총평가 ${e['total_usd']:,.0f} (≈{e['total_krw']:,.0f}원)",
        f"원금대비 {e['pnl_krw']:+,.0f}원",
    ]
    if e["rows"]:
        lines.append("\n보유:")
        for r in e["rows"]:
            lines.append(
                f"• {r['name']}({r['symbol']}) {r['qty']}주 "
                f"${r['avg_usd']}→${r['cur_usd']} ({r['pct']:+.1f}%)\n"
                f"  손익 ${r['pnl_usd']:+,.0f} / {r['pnl_krw']:+,.0f}원 "
                f"(환율효과 {r['fx_effect_krw']:+,.0f}원)"
            )
    else:
        lines.append("\n보유 종목 없음 (전액 현금)")
    return "\n".join(lines)
