"""변동성 돌파 장중 자동매매 (상주 실행).

장중에 종목별 목표가 돌파를 감시하다가, 돌파 시 시장가 매수하고
마감 직전(EXIT_TIME)에 전량 청산한다. 기본 DRY-RUN.

사용법:
    python run_breakout_live.py 005930            # 단일 종목, dry-run
    python run_breakout_live.py 005930,000660     # 여러 종목
    python run_breakout_live.py 005930 --live     # 실제 주문

환경변수:
    BREAKOUT_K     돌파 계수 (기본 0.5)
    BUDGET         종목당 배분 금액 (기본 1,000,000)
    BREAKOUT_POLL  폴링 간격 초 (기본 30)
    EXIT_TIME      청산 시각 HH:MM (기본 15:15)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.broker import KISClient
from nomad_stock.live import BreakoutLiveRunner
from nomad_stock.live.market_hours import is_market_open, market_status
from nomad_stock.notify import build_notifier


def _parse_hhmm(s: str):
    h, m = s.split(":")
    from datetime import time as dtime

    return dtime(int(h), int(m))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv
    symbols = (args[0] if args else "005930").split(",")

    k = float(os.getenv("BREAKOUT_K", "0.5"))
    budget = int(os.getenv("BUDGET", "1000000"))
    poll = int(os.getenv("BREAKOUT_POLL", "30"))
    exit_time = _parse_hhmm(os.getenv("EXIT_TIME", "15:15"))

    client = KISClient()
    effective_live = live  # 모의/실거래 모두 --live면 장중 주문 시도
    runner = BreakoutLiveRunner(client=client, dry_run=not effective_live)
    notifier = build_notifier()

    mode = "실거래" if client.cfg.env == "real" else "모의투자"
    print(
        f"변동성돌파 장중실행 | {mode} | {'실제주문' if effective_live else 'DRY-RUN'} "
        f"| k={k} 예산={budget:,} | 종목 {symbols} | {market_status()}"
    )
    if live and client.cfg.env == "real":
        print("⚠️  실거래 + 실제 주문 모드입니다. 실제 돈이 움직입니다.")

    states = {s: runner.init_state(s, k, budget) for s in symbols}
    for s, st in states.items():
        print(f"  {s}: 전일변동폭 {st.prev_range:,.0f}")

    print("\n[감시 시작] Ctrl+C로 종료.")
    try:
        while True:
            now = datetime.now()
            if not is_market_open(now):
                print(f"  {now:%H:%M:%S} 장 시간 아님 ({market_status()}) — 대기")
                time.sleep(poll)
                continue

            # 마감 직전: 보유분 청산하고 종료
            if now.time() >= exit_time:
                for st in states.values():
                    if not st.done:
                        msg = runner.close_position(st)
                        print(f"  {st.symbol}: {msg}")
                        if st.entered:
                            notifier.send(f"🔔 [돌파청산] {st.symbol}: {msg}")
                if all(st.done for st in states.values()):
                    print("[종료] 전 종목 청산 완료.")
                    break
                continue

            for st in states.values():
                if st.done:
                    continue
                msg = runner.step(st)
                print(f"  {now:%H:%M:%S} {st.symbol}: {msg}")
                if "진입" in msg:
                    notifier.send(f"🚀 [돌파진입] {st.symbol}: {msg}")
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\n[중단] 사용자 종료.")


if __name__ == "__main__":
    main()
