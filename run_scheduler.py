"""관심종목 자동매매 스케줄러.

watchlist.json을 읽어 매 거래일 지정 시각에 전 종목을 자동매매한다.
결과는 콘솔과 logs/trades.log에 함께 기록된다.

사용법:
    python run_scheduler.py                # 상주 모드, DRY-RUN (주문 안 함)
    python run_scheduler.py --once         # 지금 즉시 1회 실행 후 종료 (DRY-RUN)
    python run_scheduler.py --live         # 상주 모드, 실제 주문
    python run_scheduler.py --once --live  # 즉시 1회 실제 주문 (작업 스케줄러용)

장 시간이 아니면 실제 주문 대신 결정만 출력한다(체결 불가).
"""
from __future__ import annotations

import json
import logging
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.broker import KISClient
from nomad_stock.live import LiveRunner, RiskManager
from nomad_stock.live.market_hours import is_market_open, market_status
from nomad_stock.live.risk import RiskConfig
from nomad_stock.live.scheduler import DailyScheduler
from nomad_stock.notify import build_notifier
from nomad_stock.strategy import make_strategy

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _setup_logging() -> logging.Logger:
    os.makedirs(os.path.join(_ROOT, "logs"), exist_ok=True)
    logger = logging.getLogger("nomad_stock")
    if logger.handlers:  # 중복 핸들러 방지
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(os.path.join(_ROOT, "logs", "trades.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def _load_watchlist() -> dict:
    with open(os.path.join(_ROOT, "watchlist.json"), encoding="utf-8") as f:
        return json.load(f)


def make_job(runner: LiveRunner, items: list[dict], logger: logging.Logger, notifier, risk):
    """watchlist 전 종목을 1회 매매하는 작업 함수를 만든다."""

    def job() -> None:
        logger.info("=== 매매 사이클 시작 (%s) ===", market_status())

        # 1) 리스크 점검(손절/익절)을 전략 결정보다 먼저 — 자본 보호 우선
        if risk and risk.cfg.enabled:
            try:
                holdings = runner.client.get_balance()["holdings"]
                for hit in risk.evaluate(holdings):
                    logger.info(
                        "%s %s 발동 (손익 %+.1f%%) → 청산 %d주",
                        hit.symbol, hit.reason, hit.pnl_pct * 100, hit.qty,
                    )
                    risk.liquidate(hit, dry_run=runner.dry_run)
                    if not runner.dry_run:
                        notifier.send(
                            f"🛑 [{hit.reason}] {hit.symbol} {hit.qty}주 청산 "
                            f"(손익 {hit.pnl_pct*100:+.1f}%)"
                        )
            except Exception as e:
                logger.error("리스크 점검 실패: %r", e)

        # 2) 전략 신호에 따른 매매
        for it in items:
            symbol = it["symbol"]
            strategy = make_strategy(it.get("strategy", "sma"), **it.get("params", {}))
            budget = int(it.get("budget", 1_000_000))
            try:
                d = runner.decide(symbol, strategy, budget=budget)
                logger.info(
                    "%s 신호=%.0f 보유 %d→목표 %d ⇒ %s%s (현재가 %d)",
                    symbol, d.target_position, d.current_qty, d.target_qty,
                    d.action, f" {d.qty}주" if d.qty else "", d.price,
                )
                result = runner.execute(d)
                if result is not None:
                    odno = result.get("output", {}).get("ODNO", "?")
                    logger.info("%s 주문완료 주문번호=%s", symbol, odno)
                    notifier.send(
                        f"✅ {d.action} {symbol} {d.qty}주 @시장가"
                        f"(현재가 {d.price:,}원) 주문번호 {odno}"
                    )
            except Exception as e:
                logger.error("%s 처리 실패: %r", symbol, e)
                notifier.send(f"⚠️ {symbol} 처리 실패: {e}")
        logger.info("=== 매매 사이클 종료 ===")

    return job


def main() -> None:
    logger = _setup_logging()
    once = "--once" in sys.argv
    live = "--live" in sys.argv

    cfg = _load_watchlist()
    client = KISClient()

    # 장 시간이 아니면 실제 주문은 의미 없으므로 dry-run으로 강등
    effective_live = live and is_market_open()
    if live and not effective_live:
        logger.info("정규장 시간이 아니어서 실제 주문 대신 DRY-RUN으로 실행합니다.")

    runner = LiveRunner(client=client, dry_run=not effective_live)
    notifier = build_notifier()
    risk_cfg = RiskConfig.from_env()
    risk = RiskManager(config=risk_cfg, client=client) if risk_cfg.enabled else None
    mode = "실거래" if client.cfg.env == "real" else "모의투자"
    risk_desc = (
        f"손절 {risk_cfg.stop_loss*100:.0f}% / 익절 {risk_cfg.take_profit*100:.0f}%"
        if risk_cfg.enabled else "꺼짐"
    )
    logger.info(
        "스케줄러 모드: %s / %s / 종목 %d개 / 알림 %s / 리스크 %s",
        mode, "실제주문" if effective_live else "DRY-RUN",
        len(cfg["items"]), notifier.active_channels, risk_desc,
    )

    job = make_job(runner, cfg["items"], logger, notifier, risk)

    if once:
        job()
    else:
        DailyScheduler(run_time=cfg.get("run_time", "15:00")).run_forever(job)


if __name__ == "__main__":
    main()
