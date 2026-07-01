"""손절/익절 리스크 모니터 (단독 실행).

보유 종목의 손익률을 점검해 손절선/익절선을 넘으면 청산한다.
일봉 스케줄러는 하루 1회만 점검하므로, 장중 손절을 촘촘히 하려면 이 스크립트를
짧은 주기로 반복 실행한다(--watch).

사용법:
    python run_risk.py            # 1회 점검 (dry-run)
    python run_risk.py --live     # 1회 점검 + 실제 청산
    python run_risk.py --watch    # 장중 상주 반복 점검 (dry-run)
    python run_risk.py --watch --live

환경변수: RISK_STOP_LOSS, RISK_TAKE_PROFIT (.env), RISK_POLL(초, 기본 60)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.broker import KISClient
from nomad_stock.live import RiskManager
from nomad_stock.live.market_hours import is_market_open, market_status
from nomad_stock.live.risk import RiskConfig
from nomad_stock.notify import build_notifier


def check_once(client, risk, notifier, live: bool) -> int:
    """1회 점검. 청산한 종목 수를 반환."""
    holdings = client.get_balance()["holdings"]
    hits = risk.evaluate(holdings)
    if not hits:
        print(f"  {datetime.now():%H:%M:%S} 손절/익절 대상 없음 (보유 {len(holdings)}종목)")
        return 0
    for hit in hits:
        print(f"  {hit.symbol} {hit.reason} 손익 {hit.pnl_pct*100:+.1f}% → 청산 {hit.qty}주")
        risk.liquidate(hit, dry_run=not live)
        if live:
            notifier.send(
                f"🛑 [{hit.reason}] {hit.symbol} {hit.qty}주 청산 (손익 {hit.pnl_pct*100:+.1f}%)"
            )
    return len(hits)


def main() -> None:
    live = "--live" in sys.argv
    watch = "--watch" in sys.argv
    poll = int(os.getenv("RISK_POLL", "60"))

    cfg = RiskConfig.from_env()
    if not cfg.enabled:
        print("리스크 설정이 꺼져 있습니다. .env에 RISK_STOP_LOSS / RISK_TAKE_PROFIT 설정 필요.")
        return

    client = KISClient()
    risk = RiskManager(config=cfg, client=client)
    notifier = build_notifier()
    effective_live = live and is_market_open()

    print(
        f"리스크 모니터 | 손절 {cfg.stop_loss*100:.0f}% / 익절 {cfg.take_profit*100:.0f}% "
        f"| {'실제청산' if effective_live else 'DRY-RUN'} | {market_status()}"
    )

    if not watch:
        check_once(client, risk, notifier, effective_live)
        return

    print("[감시 시작] Ctrl+C로 종료.")
    try:
        while True:
            if is_market_open():
                check_once(client, risk, notifier, live)
            else:
                print(f"  {datetime.now():%H:%M:%S} 장 시간 아님 — 대기")
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\n[종료]")


if __name__ == "__main__":
    main()
