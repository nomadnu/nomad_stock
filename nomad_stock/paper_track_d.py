"""트랙 D: 미국 역추세 페이퍼 트레이딩 (작업지시서 트랙CD).

⚠️ 실제 주문 없음. 앱 내부 JSON 장부 (트랙 B·C와 별도).
- 매수: 볼린저 하단 터치 + RSI 과매도 (역추세)
- 물타기 금지: 이미 보유한 종목은 추가매수 안 함 (스캔에서 제외)
- 손절 -7% 자동 (역추세 필수 안전장치)
- 익절: 20일선 회복 시 매도 검토 (승인 기반)
- 원금 1,000만원 환전분, 한 종목 20%. 시세·환율은 paper_us 헬퍼 재사용.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import rules
from .paper_us import _STATE_DIR, fx_rate, position_budget_usd, us_price

_LEDGER_PATH = os.path.join(_STATE_DIR, "paper_d.json")
_RESV_PATH = os.path.join(_STATE_DIR, "d_reservations.json")


def load_ledger() -> dict:
    if os.path.exists(_LEDGER_PATH):
        try:
            with open(_LEDGER_PATH, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    fx = fx_rate()
    ledger = {
        "capital_krw": rules.DEFAULT_CAPITAL,
        "init_fx": fx,
        "cash_usd": round(rules.DEFAULT_CAPITAL / fx, 2),
        "positions": {},
        "history": [],
    }
    save_ledger(ledger)
    return ledger


def save_ledger(ledger: dict) -> None:
    with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def held_symbols() -> set:
    return set(load_ledger()["positions"].keys())


def record_buy(symbol: str, name: str, price_usd: float, fx: float) -> dict:
    ledger = load_ledger()
    if symbol in ledger["positions"]:  # 물타기 금지
        return {"ok": False, "msg": f"{symbol}: 이미 보유 중 (물타기 금지)."}
    budget = position_budget_usd(fx)
    qty = int(budget // price_usd)
    if qty < 1 or qty * price_usd > ledger["cash_usd"]:
        qty = int(ledger["cash_usd"] // price_usd)
    if qty < 1:
        return {"ok": False, "msg": f"{symbol}: 현금/한도 부족."}
    cost = round(qty * price_usd, 2)
    ledger["positions"][symbol] = {
        "name": name, "qty": qty, "avg_usd": price_usd,
        "buy_fx": fx, "buy_date": datetime.now().strftime("%Y-%m-%d"),
    }
    ledger["cash_usd"] = round(ledger["cash_usd"] - cost, 2)
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "action": "BUY", "symbol": symbol, "qty": qty, "price": price_usd, "fx": fx})
    save_ledger(ledger)
    return {"ok": True, "msg": f"📝 [역추세 매수] {name}({symbol}) {qty}주 @ ${price_usd} "
                               f"(≈{qty*price_usd*fx:,.0f}원)"}


def record_sell(symbol: str, price_usd: float, fx: float, reason: str = "매도") -> dict:
    ledger = load_ledger()
    pos = ledger["positions"].get(symbol)
    if not pos:
        return {"ok": False, "msg": f"{symbol}: 보유하고 있지 않아요."}
    qty = pos["qty"]
    pnl_usd = round((price_usd - pos["avg_usd"]) * qty, 2)
    ledger["cash_usd"] = round(ledger["cash_usd"] + qty * price_usd, 2)
    del ledger["positions"][symbol]
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "action": "SELL", "symbol": symbol, "qty": qty, "price": price_usd,
                              "fx": fx, "pnl_usd": pnl_usd, "reason": reason})
    save_ledger(ledger)
    pct = (price_usd / pos["avg_usd"] - 1) * 100 if pos["avg_usd"] else 0
    return {"ok": True, "msg": f"📝 [역추세 {reason}] {pos['name']}({symbol}) {qty}주 @ ${price_usd} "
                               f"(손익 ${pnl_usd:+,.0f}, {pct:+.1f}%)"}


def add_reservation(action: str, symbol: str, name: str) -> None:
    items = [x for x in _load_resv() if not (x["symbol"] == symbol and x["action"] == action)]
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
    items = _load_resv()
    with open(_RESV_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": []}, f)
    return items


def evaluate() -> dict:
    """현재 시세·환율로 평가 (달러·원화)."""
    ledger = load_ledger()
    fx = fx_rate()
    rows, hv = [], 0.0
    for sym, pos in ledger["positions"].items():
        try:
            cur = us_price(sym)
        except Exception:
            cur = pos["avg_usd"]
        val = cur * pos["qty"]
        hv += val
        rows.append({
            "symbol": sym, "name": pos["name"], "qty": pos["qty"],
            "avg_usd": pos["avg_usd"], "cur_usd": cur,
            "pct": (cur / pos["avg_usd"] - 1) * 100 if pos["avg_usd"] else 0,
            "pnl_krw": val * fx - pos["avg_usd"] * pos["qty"] * pos["buy_fx"],
        })
    total = ledger["cash_usd"] + hv
    return {"fx": fx, "cash_usd": ledger["cash_usd"], "total_usd": total,
            "total_krw": total * fx, "capital_krw": ledger["capital_krw"],
            "pnl_krw": total * fx - ledger["capital_krw"], "rows": rows}


def format_balance() -> str:
    e = evaluate()
    lines = [f"🔄 역추세 페이퍼 계좌 (환율 {e['fx']:.0f})",
             f"현금 ${e['cash_usd']:,.0f} · 총평가 ${e['total_usd']:,.0f} (≈{e['total_krw']:,.0f}원)",
             f"원금대비 {e['pnl_krw']:+,.0f}원"]
    if e["rows"]:
        lines.append("\n보유:")
        for r in e["rows"]:
            lines.append(f"• {r['name']}({r['symbol']}) {r['qty']}주 "
                         f"${r['avg_usd']}→${r['cur_usd']} ({r['pct']:+.1f}%)")
    else:
        lines.append("\n보유 없음 (전액 현금)")
    return "\n".join(lines)
