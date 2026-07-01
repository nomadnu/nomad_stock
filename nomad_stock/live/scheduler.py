"""거래일 지정 시각에 작업을 1회 실행하는 상주 스케줄러.

매 평일, 설정한 run_time(HH:MM)에 콜백을 한 번 실행한다.
  - 같은 날 두 번 실행되지 않도록 마지막 실행일을 기록한다.
  - 주말은 건너뛴다 (공휴일은 미반영 — 휴장일엔 주문이 자연히 거부됨).
  - 30초 간격으로 시간을 확인하므로 정밀한 초 단위 실행은 아니다.

상주 프로세스로 돌리는 방식(크로스플랫폼)이며, 윈도우 작업 스케줄러로
하루 1회 run_scheduler.py --once 를 띄우는 방식도 가능하다(README 참고).
"""
from __future__ import annotations

import time
from datetime import datetime, time as dtime
from typing import Callable

from .market_hours import market_status


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class DailyScheduler:
    def __init__(self, run_time: str = "15:00", poll_seconds: int = 30):
        self.run_time = _parse_hhmm(run_time)
        self.poll_seconds = poll_seconds
        self._last_run_date = None  # date 객체

    def _should_run(self, now: datetime) -> bool:
        if now.weekday() >= 5:  # 주말
            return False
        if self._last_run_date == now.date():  # 오늘 이미 실행함
            return False
        # 지정 시각이 지났고 같은 날이면 실행 (시작이 늦어도 그날 한 번은 실행)
        return now.time() >= self.run_time

    def run_forever(self, job: Callable[[], None]) -> None:
        """job을 매 거래일 run_time에 1회 실행하며 무한 대기."""
        print(
            f"[스케줄러] 시작 — 매 평일 {self.run_time.strftime('%H:%M')}에 실행 "
            f"(현재 {market_status()}). Ctrl+C로 종료."
        )
        try:
            while True:
                now = datetime.now()
                if self._should_run(now):
                    print(f"\n[스케줄러] {now.strftime('%Y-%m-%d %H:%M:%S')} 작업 실행")
                    try:
                        job()
                    except Exception as e:  # 하루 실패가 다음 날을 막지 않도록
                        print(f"[스케줄러] 작업 중 오류: {e!r}")
                    self._last_run_date = now.date()
                    print("[스케줄러] 완료. 다음 거래일 대기.")
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            print("\n[스케줄러] 종료됨.")
