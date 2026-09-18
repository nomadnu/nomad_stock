"""6트랙 실행 로직(웹앱 통합용). 텔레그램과 무관 — 알림은 웹 푸시로.

스캔/편입/매도/저가추적/방어선을 여기서 수행하고, 진행 후보는
state/fund6_pending.json에 저장해 대시보드가 버튼으로 보여준다.
"""
from __future__ import annotations

import json
import os
from datetime import date

from .. import fund6_watch, paper_fund6
from ..paper_us import _STATE_DIR
from ..scanner_fund6 import STRENGTH, scan_market
from ..tracks import TRACKS, tag
from . import push

_PENDING = os.path.join(_STATE_DIR, "fund6_pending.json")


def _load_pending() -> dict:
    if os.path.exists(_PENDING):
        try:
            with open(_PENDING, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_pending(d: dict) -> None:
    with open(_PENDING, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def pending() -> dict:
    """트랙별 편입 대기 후보(매수 가능·미보유). UI 버튼용."""
    return _load_pending()


def run_scan(market: str, client) -> dict:
    """시장 스캔 → 매수가능 후보 저장 + 저가감시 등록 + 푸시. 반환 {tid:[cands]}."""
    if market == "KR" and not client.is_open_day():
        return {}
    per = scan_market(market, client)
    pend = _load_pending()
    total = 0
    for tid, cands in per.items():
        led = paper_fund6.load_ledger(tid)
        held = set(led["positions"].keys())
        cash = led["cash"]
        buyable = [c for c in cands if c["symbol"] not in held and c["price"] and cash >= c["price"]]
        tn = STRENGTH[TRACKS[tid]["strength"]]["target_n"]
        for c in buyable:
            fund6_watch.add(tid, c["symbol"], c["name"], tn)
        pend[tid] = buyable
        total += len(buyable)
    _save_pending(pend)
    if total:
        flag = "🇺🇸" if market == "US" else "🇰🇷"
        push.send_push(f"{flag} 편입 후보 {total}종목", "대시보드에서 확인·편입하세요", "/")
    return per


def buy(tid: str, symbol: str) -> dict:
    if tid not in TRACKS:
        return {"ok": False, "msg": "잘못된 트랙"}
    if TRACKS[tid]["market"] == "US":
        from ..scanner import _us_meta
        name = _us_meta(symbol)[0]
    else:
        from ..scanner_kr_fund import KR_FUND_UNIVERSE
        name = KR_FUND_UNIVERSE.get(symbol, symbol)
    try:
        price = paper_fund6.price_of(tid, symbol)
    except Exception as e:
        return {"ok": False, "msg": f"가격 조회 실패: {e}"}
    tn = STRENGTH[TRACKS[tid]["strength"]]["target_n"]
    r = paper_fund6.record_buy(tid, symbol, name, price, tn, note="3박자 편입")
    if r.get("ok"):
        fund6_watch.remove(tid, symbol)
        pend = _load_pending()
        pend[tid] = [c for c in pend.get(tid, []) if c["symbol"] != symbol]
        _save_pending(pend)
    return r


def sell(tid: str, symbol: str) -> dict:
    try:
        price = paper_fund6.price_of(tid, symbol)
    except Exception as e:
        return {"ok": False, "msg": f"가격 조회 실패: {e}"}
    return paper_fund6.record_sell(tid, symbol, price)


def resume(tid: str) -> dict:
    paper_fund6.resume_track(tid)
    return {"ok": True, "msg": f"{tag(tid)} 재개"}


def pause(tid: str) -> dict:
    paper_fund6.set_paused(tid, True)
    return {"ok": True, "msg": f"{tag(tid)} 쉬기"}


def drop(tid: str, symbol: str) -> dict:
    fund6_watch.remove(tid, symbol)
    pend = _load_pending()
    pend[tid] = [c for c in pend.get(tid, []) if c["symbol"] != symbol]
    _save_pending(pend)
    return {"ok": True}


def run_low_watch(client) -> list:
    """감시 종목 저가 근처 점검 + 푸시. 반환 near-low 목록."""
    today = date.today().isoformat()
    kr_open = None
    out = []
    for tid, watch in fund6_watch.all_items().items():
        if tid not in TRACKS or not watch:
            continue
        us = TRACKS[tid]["market"] == "US"
        if not us:
            if kr_open is None:
                kr_open = client.is_open_day()
            if not kr_open:
                continue
        held = set(paper_fund6.load_ledger(tid)["positions"].keys())
        for sym, info in list(watch.items()):
            if sym in held:
                fund6_watch.remove(tid, sym)
                continue
            near, cur, low5 = fund6_watch.near_low(sym)
            if cur <= 0:
                continue
            if near and info.get("alerted") != today:
                fund6_watch.mark_alerted(tid, sym)
                out.append({"tid": tid, "symbol": sym, "name": info["name"],
                            "cur": cur, "low5": low5})
            elif (not info.get("alerted")
                  and fund6_watch.days_since(info.get("rec_date", "")) >= fund6_watch.WATCH_DAYS):
                fund6_watch.remove(tid, sym)
                out.append({"tid": tid, "symbol": sym, "name": info["name"],
                            "cur": cur, "low5": low5, "expired": True})
    if out:
        names = ", ".join(n["name"] for n in out[:5])
        push.send_push(f"📉 저가 근처 {len(out)}종목", names + " — 담기 좋은 자리", "/")
    return out


def run_defense() -> list:
    """트랙별 방어선(-15%) 점검 + 푸시. 반환 정지된 트랙 목록."""
    today = date.today().isoformat()
    hits = []
    for tid in TRACKS:
        led = paper_fund6.load_ledger(tid)
        if led.get("paused"):
            continue
        breached, ret = paper_fund6.defense_breached(tid)
        if not breached:
            if led.get("halted"):
                paper_fund6.set_halted(tid, False)
            continue
        if led.get("last_defense_date") == today:
            continue
        paper_fund6.set_halted(tid, True, "방어선(-15%) 도달")
        paper_fund6.mark_defense_date(tid)
        e = paper_fund6.evaluate(tid)
        downs = sum(1 for r in e["rows"] if r["pct"] < 0)
        hits.append({"tid": tid, "ret": round(ret, 1), "downs": downs, "holds": e["holds"]})
    if hits:
        names = ", ".join(tag(h["tid"]) for h in hits)
        push.send_push(f"🔴 방어선 도달 {len(hits)}트랙", names + " — 확인 필요", "/")
    return hits
