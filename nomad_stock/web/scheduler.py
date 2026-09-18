"""대시보드 내장 스케줄러 (텔레그램 봇 대체).

백그라운드 스레드로 시간 트리거를 돌린다:
  - 목 12:50 한국 6트랙 스캔 / 목 20:00 미국 6트랙 스캔
  - 평일 15:50 저가 추적 / 평일 16:00 트랙별 방어선
알림은 웹 푸시로 발송. 대시보드 프로세스 하나로 운용을 통일.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime

from .. import rules
from ..broker import KISClient
from . import actions

_started = False
_lock = threading.Lock()


def start() -> None:
    """중복 시작 방지하며 스케줄러 스레드 1개 기동."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="fund6-scheduler").start()
    print("[scheduler] 6트랙 스케줄러 시작")


def _loop() -> None:
    client = KISClient()
    h, m = rules.APPROVAL_TIME.split(":")
    kr_time = dtime(int(h), int(m))          # 12:50
    uh, um = rules.US_RECOMMEND_TIME.split(":")
    us_time = dtime(int(uh), int(um))        # 20:00
    low_time = dtime(15, 50)
    defense_time = dtime(16, 0)
    scan_day = rules.LONG_ALERT_DAY          # 목요일
    last: dict = {}
    while True:
        try:
            now = datetime.now()
            if now.weekday() == scan_day and now.time() >= kr_time and last.get("kr") != now.date():
                last["kr"] = now.date()
                actions.run_scan("KR", client)
            if now.weekday() == scan_day and now.time() >= us_time and last.get("us") != now.date():
                last["us"] = now.date()
                actions.run_scan("US", client)
            if now.weekday() < 5 and now.time() >= low_time and last.get("low") != now.date():
                last["low"] = now.date()
                actions.run_low_watch(client)
            if now.weekday() < 5 and now.time() >= defense_time and last.get("def") != now.date():
                last["def"] = now.date()
                actions.run_defense()
        except Exception as e:
            print(f"[scheduler] 오류: {e!r}")
        time.sleep(30)
