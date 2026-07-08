"""트랙 C: 미국 장기투자 페이퍼 가상 장부 (지침서 트랙C v0.2).

⚠️ 실제 주문 없음. 트랙 B와 별도 장부(손익 안 섞임).
- 롱온리, 5종목 이내 집중, 종목당 원금 20%
- 손절 없음 (장기 보유). "편입 근거 훼손" 시에만 승인 매도
- S&P500 대비 초과수익 추적 (벤치마크)
- 시세·환율은 paper_us와 공유(FDR)
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import FinanceDataReader as fdr

from . import rules
from .paper_us import _STATE_DIR, fx_rate, us_price

_LEDGER_PATH = os.path.join(_STATE_DIR, "paper_long.json")
_RESV_PATH = os.path.join(_STATE_DIR, "long_reservations.json")
MAX_POSITIONS = 5


# ----- 예약 (승인 → 개장+3h20m 기록, 트랙B와 동일 시각) -----
def add_reservation(action: str, symbol: str, name: str) -> None:
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
    items = _load_resv()
    with open(_RESV_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": []}, f)
    return items


def spx_level() -> float:
    """S&P500 지수 현재 레벨 (벤치마크)."""
    df = fdr.DataReader("US500", "2026-01-01")
    return round(float(df["Close"].iloc[-1]), 2)


def load_ledger() -> dict:
    if os.path.exists(_LEDGER_PATH):
        try:
            with open(_LEDGER_PATH, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    fx = fx_rate()
    try:
        spx = spx_level()
    except Exception:
        spx = 0.0
    ledger = {
        "capital_krw": rules.DEFAULT_CAPITAL,
        "init_fx": fx,
        "init_spx": spx,          # 시작 시점 S&P500 (초과수익 계산 기준)
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "cash_usd": round(rules.DEFAULT_CAPITAL / fx, 2),
        "positions": {},          # symbol -> {name, qty, avg_usd, buy_fx, buy_date, reason}
        "history": [],
    }
    save_ledger(ledger)
    return ledger


def save_ledger(ledger: dict) -> None:
    with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def record_buy(symbol: str, name: str, price_usd: float, fx: float, note: str = "") -> dict:
    """장기 편입 (원금 20%어치). 5종목 초과면 거부."""
    ledger = load_ledger()
    if symbol not in ledger["positions"] and len(ledger["positions"]) >= MAX_POSITIONS:
        return {"ok": False, "msg": f"이미 {MAX_POSITIONS}종목 집중 편입 중이라 추가 불가."}
    budget = min(rules.DEFAULT_CAPITAL * rules.MAX_POSITION_PCT, rules.MAX_POSITION_AMOUNT) / fx
    qty = int(budget // price_usd)
    if qty < 1 or qty * price_usd > ledger["cash_usd"]:
        qty = int(ledger["cash_usd"] // price_usd)
    if qty < 1:
        return {"ok": False, "msg": f"{symbol}: 현금 부족."}
    cost = round(qty * price_usd, 2)
    pos = ledger["positions"].get(symbol)
    if pos:
        tq = pos["qty"] + qty
        pos["avg_usd"] = round((pos["avg_usd"] * pos["qty"] + price_usd * qty) / tq, 2)
        pos["qty"] = tq
    else:
        ledger["positions"][symbol] = {
            "name": name, "qty": qty, "avg_usd": price_usd, "buy_fx": fx,
            "buy_date": datetime.now().strftime("%Y-%m-%d"), "reason": note,
        }
    ledger["cash_usd"] = round(ledger["cash_usd"] - cost, 2)
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "action": "BUY", "symbol": symbol, "qty": qty, "price": price_usd})
    save_ledger(ledger)
    return {"ok": True, "msg": f"📗 [장기 편입] {name}({symbol}) {qty}주 @ ${price_usd} "
                               f"(≈{qty*price_usd*fx:,.0f}원)"}


def record_sell(symbol: str, price_usd: float, fx: float, reason: str = "편입근거 훼손") -> dict:
    ledger = load_ledger()
    pos = ledger["positions"].get(symbol)
    if not pos:
        return {"ok": False, "msg": f"{symbol}: 보유하고 있지 않아요."}
    qty = pos["qty"]
    pnl_usd = round((price_usd - pos["avg_usd"]) * qty, 2)
    ledger["cash_usd"] = round(ledger["cash_usd"] + qty * price_usd, 2)
    del ledger["positions"][symbol]
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "action": "SELL",
                              "symbol": symbol, "qty": qty, "price": price_usd,
                              "pnl_usd": pnl_usd, "reason": reason})
    save_ledger(ledger)
    return {"ok": True, "msg": f"📗 [장기 매도·{reason}] {pos['name']}({symbol}) {qty}주 (손익 ${pnl_usd:+,.0f})"}


def evaluate() -> dict:
    """장부 평가 + S&P500 대비 초과수익."""
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
            "pnl_usd": (cur - pos["avg_usd"]) * pos["qty"],
            "pnl_krw": val * fx - pos["avg_usd"] * pos["qty"] * pos["buy_fx"],
            "reason": pos.get("reason", ""), "buy_date": pos.get("buy_date", ""),
        })
    total_usd = ledger["cash_usd"] + hv
    my_ret = total_usd * fx / ledger["capital_krw"] - 1
    # S&P500 수익률
    spx_ret = 0.0
    try:
        if ledger.get("init_spx"):
            spx_ret = spx_level() / ledger["init_spx"] - 1
    except Exception:
        pass
    return {
        "fx": fx, "cash_usd": ledger["cash_usd"], "total_usd": total_usd,
        "total_krw": total_usd * fx, "capital_krw": ledger["capital_krw"],
        "pnl_krw": total_usd * fx - ledger["capital_krw"],
        "my_ret": my_ret * 100, "spx_ret": spx_ret * 100,
        "excess": (my_ret - spx_ret) * 100, "rows": rows,
        "start_date": ledger.get("start_date", ""),
    }


def format_balance() -> str:
    e = evaluate()
    lines = [
        f"📗 미국 장기투자 페이퍼 (시작 {e['start_date']} · 환율 {e['fx']:.0f})",
        f"총평가 ${e['total_usd']:,.0f} (≈{e['total_krw']:,.0f}원) · 원금대비 {e['pnl_krw']:+,.0f}원",
        f"내 수익률 {e['my_ret']:+.1f}% vs S&P500 {e['spx_ret']:+.1f}% → 초과 {e['excess']:+.1f}%p",
    ]
    if e["rows"]:
        lines.append(f"\n보유 {len(e['rows'])}/{MAX_POSITIONS}종목:")
        for r in e["rows"]:
            lines.append(
                f"• {r['name']}({r['symbol']}) {r['qty']}주 ${r['avg_usd']}→${r['cur_usd']} "
                f"({r['pct']:+.1f}%, {r['pnl_krw']:+,.0f}원)"
            )
    else:
        lines.append("\n보유 없음 (전액 현금) — 3박자 통과 종목 없으면 억지 편입 안 함")
    return "\n".join(lines)
