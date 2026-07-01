"""멀티 종목 포트폴리오 자동매매 (리밸런싱).

portfolio.json의 총자본·비중·전략에 따라 전 종목을 목표 비중으로 맞춘다.
기본 DRY-RUN. 장 시간이 아니면 결정만 출력한다.

사용법:
    python run_portfolio.py            # 현황 + 리밸런싱 결정 (dry-run)
    python run_portfolio.py --status   # 현재 보유 현황만 출력
    python run_portfolio.py --live     # 실제 리밸런싱 주문
"""
from __future__ import annotations

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.broker import KISClient
from nomad_stock.live import PortfolioRunner, RiskManager
from nomad_stock.live.market_hours import is_market_open, market_status
from nomad_stock.live.portfolio import validate_weights
from nomad_stock.live.risk import RiskConfig
from nomad_stock.notify import build_notifier

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load() -> dict:
    with open(os.path.join(_ROOT, "portfolio.json"), encoding="utf-8") as f:
        return json.load(f)


def _print_status(pr: PortfolioRunner, items: list[dict]) -> None:
    st = pr.status(items)
    print(f"\n예수금 {st['cash']:,}원 / 총평가 {st['total_eval']:,}원")
    print(f"{'종목':<10}{'목표비중':>10}{'현재비중':>10}{'평가금액':>14}{'수량':>8}")
    print("-" * 52)
    for r in st["rows"]:
        print(
            f"{r['symbol']:<10}{r['target_weight']*100:>9.0f}%"
            f"{r['actual_weight']*100:>9.1f}%{r['value']:>14,}{r['qty']:>8}"
        )


def main() -> None:
    cfg = _load()
    items = cfg["items"]
    validate_weights(items)

    live = "--live" in sys.argv
    status_only = "--status" in sys.argv

    client = KISClient()
    effective_live = live and is_market_open()
    if live and not effective_live:
        print("정규장 시간이 아니어서 DRY-RUN으로 실행합니다.")

    pr = PortfolioRunner(
        total_capital=int(cfg["total_capital"]),
        client=client,
        dry_run=not effective_live,
    )
    notifier = build_notifier()

    mode = "실거래" if client.cfg.env == "real" else "모의투자"
    print(
        f"포트폴리오 | {mode} | {'실제주문' if effective_live else 'DRY-RUN'} "
        f"| 총자본 {int(cfg['total_capital']):,}원 | {market_status()}"
    )

    _print_status(pr, items)
    if status_only:
        return

    # 리스크 점검(손절/익절)을 리밸런싱보다 먼저
    risk_cfg = RiskConfig.from_env()
    if risk_cfg.enabled:
        risk = RiskManager(config=risk_cfg, client=client)
        print(f"\n[리스크 점검] 손절 {risk_cfg.stop_loss*100:.0f}% / 익절 {risk_cfg.take_profit*100:.0f}%")
        for hit in risk.evaluate(client.get_balance()["holdings"]):
            print(f"  {hit.symbol} {hit.reason} (손익 {hit.pnl_pct*100:+.1f}%) → 청산 {hit.qty}주")
            risk.liquidate(hit, dry_run=not effective_live)
            if effective_live:
                notifier.send(f"🛑 [{hit.reason}] {hit.symbol} {hit.qty}주 청산 (손익 {hit.pnl_pct*100:+.1f}%)")

    print("\n[리밸런싱]")
    decisions = pr.rebalance(items)
    for d in decisions:
        line = (
            f"  {d.symbol}: 신호={d.target_position:.0f} "
            f"보유 {d.current_qty}→목표 {d.target_qty} ⇒ {d.action}"
            + (f" {d.qty}주" if d.qty else "")
        )
        print(line)
        if effective_live and d.action != "HOLD" and d.qty > 0:
            notifier.send(f"⚖️ [리밸런싱] {d.action} {d.symbol} {d.qty}주")


if __name__ == "__main__":
    main()
