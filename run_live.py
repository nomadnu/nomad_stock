"""라이브/페이퍼 트레이딩 실행.

기본은 DRY-RUN(주문 계산만, 실제 주문 안 함). 실제 주문하려면 --live 플래그.

사용법:
    python run_live.py                       # 삼성전자, SMA20x60, dry-run
    python run_live.py 000660 10 30          # SK하이닉스, SMA10x30, dry-run
    python run_live.py 005930 20 60 --live   # 실제 주문 (모의/실거래는 .env의 KIS_ENV)

배분 금액(budget)은 BUDGET 환경변수로 조정 (기본 1,000,000원).
"""
from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.broker import KISClient
from nomad_stock.live import LiveRunner
from nomad_stock.live.market_hours import is_market_open, market_status
from nomad_stock.strategy import SmaCrossStrategy


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv

    symbol = args[0] if len(args) > 0 else "005930"
    fast = int(args[1]) if len(args) > 1 else 20
    slow = int(args[2]) if len(args) > 2 else 60
    budget = int(os.getenv("BUDGET", "1000000"))

    client = KISClient()
    runner = LiveRunner(client=client, dry_run=not live)

    mode = "실거래" if client.cfg.env == "real" else "모의투자"
    order_mode = "실제 주문" if live else "DRY-RUN (주문 안 함)"
    print(f"환경: {mode} / {order_mode} / {market_status()}")
    if live and client.cfg.env == "real":
        print("⚠️  실거래 + 실제 주문 모드입니다. 실제 돈이 움직입니다.")

    # 실제 주문인데 장 시간이 아니면 미리 막는다 (체결 불가).
    if live and not is_market_open():
        print("→ 정규장 시간이 아니라 주문이 체결되지 않습니다. DRY-RUN으로 결정만 출력합니다.")
        runner.dry_run = True

    strategy = SmaCrossStrategy(fast=fast, slow=slow)
    runner.run_once(symbol, strategy, budget=budget)


if __name__ == "__main__":
    main()
