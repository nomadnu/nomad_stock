"""저가 추적 알림 (지침서 v1.4): 편입 후보를 5거래일 지켜보다 저점 근처에 알림.

- 주간 스캔에서 뜬 후보(아직 미보유)를 감시 목록에 등록.
- 매일 점검: 최근 5거래일 저가 +1% 이내면 '저가 근처' 알림 → 편입 or 더 기다림.
- 5거래일(≈7달력일) 지나도 저가 알림 없으면 "현재가로 살까요/다음주로?" 문의.
- 편입/드롭 시 감시 해제. 저장: state/fund6_watch.json.
"""
from __future__ import annotations

import json
import os
from datetime import date

import FinanceDataReader as fdr

from .paper_us import _STATE_DIR

_PATH = os.path.join(_STATE_DIR, "fund6_watch.json")
NEAR_LOW_PCT = 0.01   # 최근 5거래일 저가 +1% 이내면 '저가 근처'
WATCH_DAYS = 7        # 달력 7일(≈5거래일) 지나면 만료 문의


def _load() -> dict:
    if os.path.exists(_PATH):
        try:
            with open(_PATH, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}


def _save(d: dict) -> None:
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def add(tid: str, symbol: str, name: str, target_n: int) -> None:
    """감시 등록(이미 있으면 유지)."""
    d = _load()
    d.setdefault(tid, {})
    if symbol not in d[tid]:
        d[tid][symbol] = {"name": name, "rec_date": date.today().isoformat(),
                          "target_n": target_n, "alerted": ""}
        _save(d)


def remove(tid: str, symbol: str) -> None:
    d = _load()
    if d.get(tid, {}).pop(symbol, None) is not None:
        _save(d)


def items(tid: str) -> dict:
    return _load().get(tid, {})


def all_items() -> dict:
    return _load()


def mark_alerted(tid: str, symbol: str) -> None:
    d = _load()
    if symbol in d.get(tid, {}):
        d[tid][symbol]["alerted"] = date.today().isoformat()
        _save(d)


def near_low(symbol: str) -> tuple[bool, float, float]:
    """(저가 근처인가, 현재가, 최근 5거래일 저가). 실패 시 (False, 0, 0)."""
    try:
        df = fdr.DataReader(symbol, "2026-01-01")
        low5 = float(df["Low"].tail(5).min())
        cur = float(df["Close"].iloc[-1])
        return (cur <= low5 * (1 + NEAR_LOW_PCT), round(cur, 2), round(low5, 2))
    except Exception:
        return (False, 0.0, 0.0)


def days_since(rec_date: str) -> int:
    try:
        return (date.today() - date.fromisoformat(rec_date)).days
    except Exception:
        return 0
