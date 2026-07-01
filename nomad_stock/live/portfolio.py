"""멀티 종목 포트폴리오 리밸런싱.

총자본(total_capital)을 종목별 목표 비중(weight)으로 배분한다.
각 종목의 전략 신호(0/1)를 곱해 '목표 보유금액'을 정하고, 현재 보유와의
차이를 메우는 매수/매도 주문을 낸다.
  목표금액 = total_capital * weight * signal
  목표수량 = 목표금액 // 현재가
신호가 0이 되면 목표수량 0 → 자동 청산. 신호가 1이면 비중만큼 매수.

LiveRunner.decide를 종목별로 재사용한다(예산 = total_capital * weight).
"""
from __future__ import annotations

from ..broker import KISClient
from ..strategy import make_strategy
from .runner import LiveRunner, TradeDecision


def validate_weights(items: list[dict]) -> None:
    total = sum(float(it.get("weight", 0)) for it in items)
    if total > 1.0 + 1e-9:
        raise ValueError(f"비중 합이 1을 초과합니다: {total:.3f}")


class PortfolioRunner:
    def __init__(
        self,
        total_capital: int,
        client: KISClient | None = None,
        dry_run: bool = True,
    ):
        self.total_capital = total_capital
        self.runner = LiveRunner(client=client or KISClient(), dry_run=dry_run)

    @property
    def client(self) -> KISClient:
        return self.runner.client

    def rebalance(self, items: list[dict]) -> list[TradeDecision]:
        """전 종목을 목표 비중으로 리밸런싱. 결정 리스트를 반환."""
        validate_weights(items)
        decisions = []
        for it in items:
            budget = int(self.total_capital * float(it["weight"]))
            strategy = make_strategy(it.get("strategy", "sma"), **it.get("params", {}))
            d = self.runner.decide(it["symbol"], strategy, budget=budget)
            decisions.append(d)
            self.runner.execute(d)
        return decisions

    def status(self, items: list[dict]) -> dict:
        """현재 계좌의 종목별 평가금액과 목표 비중 대비 현황."""
        bal = self.client.get_balance()
        held = {h["symbol"]: h for h in bal["holdings"]}
        total_eval = bal["total_eval"] or self.total_capital
        rows = []
        for it in items:
            sym = it["symbol"]
            h = held.get(sym)
            value = (h["qty"] * h["cur_price"]) if h else 0
            rows.append(
                {
                    "symbol": sym,
                    "target_weight": float(it["weight"]),
                    "actual_weight": value / total_eval if total_eval else 0.0,
                    "value": value,
                    "qty": h["qty"] if h else 0,
                }
            )
        return {"cash": bal["cash"], "total_eval": total_eval, "rows": rows}
