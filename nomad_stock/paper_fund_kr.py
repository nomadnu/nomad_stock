"""트랙 D(신규): 한국 펀더멘털 페이퍼 가상 장부 (원화, 실제 주문 없음).

트랙 C(미국 펀더멘털)의 한국판. 롱온리, 5종목 이내 집중, 종목당 원금 20%.
손절 없음(장기) — "이유가 바뀌어서"만 매도. 코스피200 대비 초과수익 추적.
한국장이라 환전 없음(원화 그대로). 시세는 FDR. 트랙 A/B/C와 장부 분리.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import FinanceDataReader as fdr

from . import rules
from .paper_us import _STATE_DIR

_LEDGER_PATH = os.path.join(_STATE_DIR, "paper_fund_kr.json")
MAX_POSITIONS = 5


def kr_price(code: str) -> float:
    """한국 종목 현재가(원). FDR 최근 종가."""
    df = fdr.DataReader(code, "2026-01-01")
    return round(float(df["Close"].iloc[-1]))


def kospi200_level() -> float:
    """코스피200 지수 레벨 (벤치마크). ⚠️ 무료 데이터 불안정 — 참고용."""
    df = fdr.DataReader("KS200", "2026-01-01")
    return round(float(df["Close"].iloc[-1]), 2)


def load_ledger() -> dict:
    if os.path.exists(_LEDGER_PATH):
        try:
            with open(_LEDGER_PATH, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    try:
        ks = kospi200_level()
    except Exception:
        ks = 0.0
    ledger = {
        "capital_krw": rules.DEFAULT_CAPITAL,
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "init_kospi200": ks,       # 시작 시점 코스피200 (초과수익 기준, 불안정할 수 있음)
        "cash_krw": rules.DEFAULT_CAPITAL,
        "positions": {},           # code -> {name, qty, avg_krw, buy_date, reason}
        "history": [],
    }
    save_ledger(ledger)
    return ledger


def save_ledger(ledger: dict) -> None:
    with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def held_codes() -> set:
    return set(load_ledger()["positions"].keys())


def record_buy(code: str, name: str, price_krw: float, note: str = "") -> dict:
    """장기 편입 (원금 20%어치). 5종목 초과면 거부. 한국장이라 즉시 기록."""
    ledger = load_ledger()
    if code not in ledger["positions"] and len(ledger["positions"]) >= MAX_POSITIONS:
        return {"ok": False, "msg": f"이미 {MAX_POSITIONS}종목 집중 편입 중이라 추가 불가."}
    budget = min(rules.DEFAULT_CAPITAL * rules.MAX_POSITION_PCT, rules.MAX_POSITION_AMOUNT)
    qty = int(budget // price_krw) if price_krw > 0 else 0
    if qty < 1 or qty * price_krw > ledger["cash_krw"]:
        qty = int(ledger["cash_krw"] // price_krw) if price_krw > 0 else 0
    if qty < 1:
        return {"ok": False, "msg": f"{name}({code}): 현금 부족 또는 가격 오류."}
    cost = round(qty * price_krw)
    pos = ledger["positions"].get(code)
    if pos:
        tq = pos["qty"] + qty
        pos["avg_krw"] = round((pos["avg_krw"] * pos["qty"] + price_krw * qty) / tq)
        pos["qty"] = tq
    else:
        ledger["positions"][code] = {
            "name": name, "qty": qty, "avg_krw": round(price_krw),
            "buy_date": datetime.now().strftime("%Y-%m-%d"), "reason": note,
        }
    ledger["cash_krw"] = round(ledger["cash_krw"] - cost)
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "action": "BUY", "code": code, "qty": qty, "price": round(price_krw)})
    save_ledger(ledger)
    return {"ok": True, "msg": f"📘 [한국 펀더멘털 편입] {name}({code}) {qty}주 @ {price_krw:,.0f}원 "
                               f"(≈{cost:,}원)"}


def record_sell(code: str, price_krw: float, reason: str = "편입근거 훼손") -> dict:
    ledger = load_ledger()
    pos = ledger["positions"].get(code)
    if not pos:
        return {"ok": False, "msg": f"{code}: 보유하고 있지 않아요."}
    qty = pos["qty"]
    pnl = round((price_krw - pos["avg_krw"]) * qty)
    ledger["cash_krw"] = round(ledger["cash_krw"] + qty * price_krw)
    del ledger["positions"][code]
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "action": "SELL",
                              "code": code, "qty": qty, "price": round(price_krw),
                              "pnl_krw": pnl, "reason": reason})
    save_ledger(ledger)
    return {"ok": True, "msg": f"📘 [한국 펀더멘털 매도·{reason}] {pos['name']}({code}) {qty}주 "
                               f"(손익 {pnl:+,}원)"}


def evaluate() -> dict:
    """장부 평가 + 코스피200 대비 초과수익(불안정 데이터라 참고용)."""
    ledger = load_ledger()
    rows, hv = [], 0.0
    for code, pos in ledger["positions"].items():
        try:
            cur = kr_price(code)
        except Exception:
            cur = pos["avg_krw"]
        val = cur * pos["qty"]
        hv += val
        rows.append({
            "code": code, "name": pos["name"], "qty": pos["qty"],
            "avg_krw": pos["avg_krw"], "cur_krw": cur,
            "pct": (cur / pos["avg_krw"] - 1) * 100 if pos["avg_krw"] else 0,
            "pnl_krw": (cur - pos["avg_krw"]) * pos["qty"],
            "reason": pos.get("reason", ""), "buy_date": pos.get("buy_date", ""),
        })
    total_krw = ledger["cash_krw"] + hv
    my_ret = total_krw / ledger["capital_krw"] - 1
    ks_ret = 0.0
    try:
        if ledger.get("init_kospi200"):
            ks_ret = kospi200_level() / ledger["init_kospi200"] - 1
    except Exception:
        pass
    return {
        "cash_krw": ledger["cash_krw"], "total_krw": total_krw,
        "capital_krw": ledger["capital_krw"],
        "pnl_krw": total_krw - ledger["capital_krw"],
        "my_ret": my_ret * 100, "ks_ret": ks_ret * 100,
        "excess": (my_ret - ks_ret) * 100, "rows": rows,
        "start_date": ledger.get("start_date", ""),
    }


def format_balance() -> str:
    e = evaluate()
    lines = [
        f"📘 한국 펀더멘털 페이퍼 (시작 {e['start_date']})",
        f"현금 {e['cash_krw']:,.0f}원 · 총평가 {e['total_krw']:,.0f}원 · 원금대비 {e['pnl_krw']:+,.0f}원",
        f"내 수익률 {e['my_ret']:+.1f}% vs 코스피200 {e['ks_ret']:+.1f}% → 초과 {e['excess']:+.1f}%p ⚠️(지수 데이터 참고용)",
    ]
    if e["rows"]:
        lines.append(f"\n보유 {len(e['rows'])}/{MAX_POSITIONS}종목:")
        for r in e["rows"]:
            lines.append(
                f"• {r['name']}({r['code']}) {r['qty']}주 {r['avg_krw']:,}→{r['cur_krw']:,}원 "
                f"({r['pct']:+.1f}%, {r['pnl_krw']:+,.0f}원)"
            )
    else:
        lines.append("\n보유 없음 (전액 현금) — 3박자 통과 없으면 억지 편입 안 함")
    return "\n".join(lines)
