"""펀더멘털 6트랙 공용 페이퍼 장부 (지침서 v1.4).

트랙 하나당 파일: state/fund6_<track_id>.json. 미국=달러(+환율), 한국=원화.
- 손절 없음(장기). 방어선 트랙별 독립 -15%(-150만). halted/paused를 장부에 보관.
- 잔고 기반 1주 매수(소수점 없음). 종목당 최대 20%, 편입수 균등.
- 벤치마크: 미국 S&P500 / 한국 코스피200(불안정→참고).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import rules
from .paper_fund_kr import kospi200_level, kr_price
from .paper_long import spx_level
from .paper_us import _STATE_DIR, fx_rate, us_price
from .tracks import TRACKS, is_us

DEFENSE_PCT = 0.15          # 트랙별 방어선 -15%
MAX_POSITION_PCT = 0.20     # 종목당 최대 비중


def _path(tid: str) -> str:
    return os.path.join(_STATE_DIR, f"fund6_{tid}.json")


def load_ledger(tid: str) -> dict:
    p = _path(tid)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    cap = rules.DEFAULT_CAPITAL
    if is_us(tid):
        try:
            fx = fx_rate()
        except Exception:
            fx = 1350.0
        try:
            bench = spx_level()
        except Exception:
            bench = 0.0
        ledger = {"track_id": tid, "market": "US", "capital_krw": cap,
                  "init_fx": fx, "init_bench": bench, "cash": round(cap / fx, 2)}
    else:
        try:
            bench = kospi200_level()
        except Exception:
            bench = 0.0
        ledger = {"track_id": tid, "market": "KR", "capital_krw": cap,
                  "init_bench": bench, "cash": cap}
    ledger.update({"start_date": datetime.now().strftime("%Y-%m-%d"),
                   "positions": {}, "history": [],
                   "halted": False, "halt_reason": "", "paused": False,
                   "last_defense_date": ""})
    save_ledger(tid, ledger)
    return ledger


def save_ledger(tid: str, ledger: dict) -> None:
    with open(_path(tid), "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def held_symbols(tid: str) -> set:
    return set(load_ledger(tid)["positions"].keys())


def price_of(tid: str, symbol: str) -> float:
    return us_price(symbol) if is_us(tid) else kr_price(symbol)


def position_budget(tid: str, target_n: int, fx: float | None = None) -> float:
    """한 종목 매수 예산(트랙 통화). 종목당 min(원금20%, 원금/편입수)."""
    cap = rules.DEFAULT_CAPITAL
    budget_krw = min(cap * MAX_POSITION_PCT, cap / max(target_n, 1))
    if is_us(tid):
        return budget_krw / (fx or fx_rate())
    return budget_krw


def can_afford_one(tid: str, price: float) -> bool:
    """남은 현금으로 1주라도 살 수 있나 (잔고 기반 1주 매수 규칙)."""
    return load_ledger(tid)["cash"] >= price > 0


def record_buy(tid: str, symbol: str, name: str, price: float, target_n: int,
               note: str = "") -> dict:
    """1주 단위 편입. 예산·현금 내에서 정수 수량. 정지·쉬기 중이면 거부."""
    ledger = load_ledger(tid)
    if ledger.get("paused"):
        return {"ok": False, "msg": f"{name}: 이 트랙은 '쉬기' 상태예요."}
    if ledger.get("halted"):
        return {"ok": False, "msg": f"{name}: 이 트랙은 방어선 정지 상태예요('재개' 후 가능)."}
    fx = fx_rate() if is_us(tid) else None
    budget = position_budget(tid, target_n, fx)
    qty = int(budget // price) if price > 0 else 0
    if qty * price > ledger["cash"]:
        qty = int(ledger["cash"] // price) if price > 0 else 0
    if qty < 1:
        return {"ok": False, "msg": f"{name}({symbol}): 현금 부족 — 1주도 못 삽니다."}
    cost = round(qty * price, 2)
    pos = ledger["positions"].get(symbol)
    if pos:
        tq = pos["qty"] + qty
        pos["avg"] = round((pos["avg"] * pos["qty"] + price * qty) / tq, 2)
        pos["qty"] = tq
    else:
        pos = {"name": name, "qty": qty, "avg": round(price, 2),
               "buy_date": datetime.now().strftime("%Y-%m-%d"), "reason": note}
        if is_us(tid):
            pos["buy_fx"] = fx
        ledger["positions"][symbol] = pos
    ledger["cash"] = round(ledger["cash"] - cost, 2)
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "action": "BUY", "symbol": symbol, "qty": qty, "price": round(price, 2)})
    save_ledger(tid, ledger)
    unit = "$" if is_us(tid) else "원"
    return {"ok": True, "msg": f"📗 편입 {name}({symbol}) {qty}주 @ {price:,.2f}{unit}"}


def record_sell(tid: str, symbol: str, price: float, reason: str = "근거 훼손") -> dict:
    ledger = load_ledger(tid)
    pos = ledger["positions"].get(symbol)
    if not pos:
        return {"ok": False, "msg": f"{symbol}: 보유하고 있지 않아요."}
    qty = pos["qty"]
    pnl = round((price - pos["avg"]) * qty, 2)
    ledger["cash"] = round(ledger["cash"] + qty * price, 2)
    del ledger["positions"][symbol]
    ledger["history"].append({"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "action": "SELL",
                              "symbol": symbol, "qty": qty, "price": round(price, 2),
                              "pnl": pnl, "reason": reason})
    save_ledger(tid, ledger)
    unit = "$" if is_us(tid) else "원"
    return {"ok": True, "msg": f"📕 매도·{reason} {pos['name']}({symbol}) {qty}주 (손익 {pnl:+,.2f}{unit})"}


# ----- 트랙별 방어선 상태 -----
def set_halted(tid: str, halted: bool, reason: str = "") -> None:
    led = load_ledger(tid)
    led["halted"], led["halt_reason"] = halted, reason
    save_ledger(tid, led)


def set_paused(tid: str, paused: bool) -> None:
    led = load_ledger(tid)
    led["paused"] = paused
    if paused:
        led["halted"], led["halt_reason"] = True, "이 트랙 쉬기(수동)"
    save_ledger(tid, led)


def resume_track(tid: str) -> None:
    """재개: 정지·쉬기 모두 해제(사람이 확인 후 수동 재개)."""
    led = load_ledger(tid)
    led["halted"], led["halt_reason"], led["paused"] = False, "", False
    save_ledger(tid, led)


def mark_defense_date(tid: str) -> None:
    led = load_ledger(tid)
    led["last_defense_date"] = datetime.now().strftime("%Y-%m-%d")
    save_ledger(tid, led)


def evaluate(tid: str) -> dict:
    """정규화된 평가 결과(원화 기준 + 트랙 통화 표시)."""
    led = load_ledger(tid)
    us = led["market"] == "US"
    fx = fx_rate() if us else 1.0
    rows, hv = [], 0.0
    for sym, pos in led["positions"].items():
        try:
            cur = us_price(sym) if us else kr_price(sym)
        except Exception:
            cur = pos["avg"]
        hv += cur * pos["qty"]
        unit = "$" if us else ""
        suf = "" if us else "원"
        rows.append({
            "symbol": sym, "name": pos["name"], "qty": pos["qty"],
            "buy_disp": f"{unit}{pos['avg']:,.2f}{suf}" if us else f"{pos['avg']:,.0f}원",
            "cur_disp": f"{unit}{cur:,.2f}{suf}" if us else f"{cur:,.0f}원",
            "pct": (cur / pos["avg"] - 1) * 100 if pos["avg"] else 0,
            "buy_date": pos.get("buy_date", ""), "reason": pos.get("reason", ""),
        })
    total_native = led["cash"] + hv
    total_krw = total_native * fx if us else total_native
    cap = led["capital_krw"]
    ret = (total_krw / cap - 1) * 100 if cap else 0
    # 벤치마크
    bench_ret = 0.0
    try:
        cur_bench = spx_level() if us else kospi200_level()
        if led.get("init_bench"):
            bench_ret = (cur_bench / led["init_bench"] - 1) * 100
    except Exception:
        pass
    cash_krw = (led["cash"] * fx) if us else led["cash"]
    return {
        "track_id": tid, "market": led["market"], "capital_krw": cap,
        "total_krw": total_krw, "pnl_krw": total_krw - cap, "ret": ret,
        "cash_pct": (cash_krw / total_krw * 100) if total_krw else 0,
        "holds": len(rows), "rows": rows,
        "bench_ret": bench_ret, "excess": ret - bench_ret,
        "halted": led.get("halted", False), "halt_reason": led.get("halt_reason", ""),
        "paused": led.get("paused", False), "start_date": led.get("start_date", ""),
        "fx": fx if us else None, "last_defense_date": led.get("last_defense_date", ""),
    }


def defense_breached(tid: str) -> tuple[bool, float]:
    """방어선(-15%) 도달 여부와 현재 수익률%."""
    e = evaluate(tid)
    return (e["ret"] <= -DEFENSE_PCT * 100), e["ret"]


def format_balance(tid: str) -> str:
    from .tracks import tag
    e = evaluate(tid)
    bench_name = "S&P500" if e["market"] == "US" else "코스피200"
    warn = " ⚠️(지수 참고용)" if e["market"] == "KR" else ""
    state = "🔴 정지" if e["halted"] else "🟢 가동"
    if e["paused"]:
        state = "⏸ 쉬기"
    lines = [
        f"{tag(tid)} 계좌 ({state} · 시작 {e['start_date']})",
        f"총평가 {e['total_krw']:,.0f}원 · 원금대비 {e['pnl_krw']:+,.0f}원 ({e['ret']:+.1f}%)",
        f"vs {bench_name} {e['bench_ret']:+.1f}% → 초과 {e['excess']:+.1f}%p{warn}",
    ]
    if e["rows"]:
        lines.append(f"\n보유 {e['holds']}종목:")
        for r in e["rows"]:
            lines.append(f"• {r['name']}({r['symbol']}) {r['qty']}주 {r['buy_disp']}→{r['cur_disp']} ({r['pct']:+.1f}%)")
    else:
        lines.append("\n보유 없음 (전액 현금)")
    return "\n".join(lines)
