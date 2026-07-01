"""손절/익절 리스크 관리.

보유 종목의 손익률(현재가/평균매입가 - 1)을 점검해, 손절선/익절선을 넘으면
'강제 청산' 대상으로 표시한다. 전략 신호보다 우선 적용해 자본을 보호한다.

매 매매 사이클(스케줄러/포트폴리오)에서 전략 결정 '이전'에 호출하는 것을 권장.
일봉 스케줄러는 하루 1회 점검이므로, 더 촘촘한 손절이 필요하면 별도 모니터를
짧은 주기로 돌리면 된다(run_risk.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..broker import KISClient


@dataclass
class RiskConfig:
    stop_loss: float = 0.05      # -5% 손절 (0이면 끔)
    take_profit: float = 0.10    # +10% 익절 (0이면 끔)

    @classmethod
    def from_env(cls) -> "RiskConfig":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

        def _f(name: str) -> float:
            v = os.getenv(name, "").strip()
            try:
                return float(v) if v else 0.0
            except ValueError:
                return 0.0

        return cls(stop_loss=_f("RISK_STOP_LOSS"), take_profit=_f("RISK_TAKE_PROFIT"))

    @property
    def enabled(self) -> bool:
        return self.stop_loss > 0 or self.take_profit > 0


@dataclass
class RiskHit:
    symbol: str
    qty: int
    avg_price: float
    cur_price: int
    pnl_pct: float
    reason: str   # "손절" | "익절"


class RiskManager:
    def __init__(self, config: RiskConfig | None = None, client: KISClient | None = None):
        self.cfg = config or RiskConfig.from_env()
        self.client = client or KISClient()

    def evaluate(self, holdings: list[dict]) -> list[RiskHit]:
        """잔고 보유내역에서 손절/익절 대상 목록을 산출한다.

        holdings: KISClient.get_balance()['holdings'] 형식
            (symbol, qty, avg_price, cur_price ...)
        """
        hits = []
        for h in holdings:
            avg = float(h["avg_price"])
            if avg <= 0 or h["qty"] <= 0:
                continue
            pnl = h["cur_price"] / avg - 1.0
            reason = None
            if self.cfg.stop_loss > 0 and pnl <= -self.cfg.stop_loss:
                reason = "손절"
            elif self.cfg.take_profit > 0 and pnl >= self.cfg.take_profit:
                reason = "익절"
            if reason:
                hits.append(
                    RiskHit(
                        symbol=h["symbol"],
                        qty=h["qty"],
                        avg_price=avg,
                        cur_price=h["cur_price"],
                        pnl_pct=pnl,
                        reason=reason,
                    )
                )
        return hits

    def liquidate(self, hit: RiskHit, dry_run: bool = True) -> dict | None:
        """리스크 대상 종목을 전량 시장가 매도한다."""
        if dry_run:
            print(
                f"[DRY-RUN] {hit.reason} 청산 SELL {hit.symbol} {hit.qty}주 "
                f"(손익 {hit.pnl_pct*100:+.1f}%) — 실제 주문 안 함"
            )
            return None
        result = self.client.order_cash(hit.symbol, hit.qty, "sell", order_type="01")
        odno = result.get("output", {}).get("ODNO", "?")
        print(f"[{hit.reason} 청산] SELL {hit.symbol} {hit.qty}주 — 주문번호 {odno}")
        return result
