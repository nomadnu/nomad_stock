"""라이브/페이퍼 트레이딩 러너.

백테스트와 '동일한' Strategy 객체를 받아서:
  1) 최근 일봉(FDR)으로 오늘의 목표 포지션 신호를 계산하고
  2) 현재 계좌 보유 상태와 비교해
  3) 차이를 메우는 매수/매도 주문을 KIS로 낸다.

안전장치:
  - dry_run=True(기본): 주문을 '계산만' 하고 실제로는 안 낸다.
  - 단일 종목, 단일 신호 기준의 가장 단순한 실행. 분할매수/리밸런싱은 추후 확장.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..broker import KISClient
from ..data.loader import load_ohlcv
from ..strategy.base import Strategy


@dataclass
class TradeDecision:
    symbol: str
    target_position: float   # 전략이 원하는 목표 (0=현금, 1=풀매수)
    current_qty: int         # 현재 보유 수량
    target_qty: int          # 목표 보유 수량
    action: str              # "BUY" | "SELL" | "HOLD"
    qty: int                 # 주문 수량(절대값)
    price: int               # 참고용 현재가


class LiveRunner:
    def __init__(self, client: KISClient | None = None, dry_run: bool = True):
        self.client = client or KISClient()
        self.dry_run = dry_run

    def decide(
        self,
        symbol: str,
        strategy: Strategy,
        budget: int,
    ) -> TradeDecision:
        """오늘 신호를 계산하고 목표 대비 필요한 주문을 산출한다.

        budget: 이 종목에 배분할 금액(원). 목표 수량 = budget // 현재가 * 목표비중.
        """
        # 최근 약 1년 일봉으로 신호 계산 (캐시 안 씀: 최신 종가 반영 위해)
        df = load_ohlcv(symbol, start="2023-01-01", use_cache=False)
        signal = strategy.generate_signals(df)
        target_position = float(signal.iloc[-1])  # 가장 최근 신호

        price = self.client.get_price(symbol)
        bal = self.client.get_balance()
        current_qty = next(
            (h["qty"] for h in bal["holdings"] if h["symbol"] == symbol), 0
        )

        max_qty = budget // price if price > 0 else 0
        target_qty = int(max_qty * target_position)

        diff = target_qty - current_qty
        if diff > 0:
            action, qty = "BUY", diff
        elif diff < 0:
            action, qty = "SELL", -diff
        else:
            action, qty = "HOLD", 0

        return TradeDecision(
            symbol=symbol,
            target_position=target_position,
            current_qty=current_qty,
            target_qty=target_qty,
            action=action,
            qty=qty,
            price=price,
        )

    def execute(self, decision: TradeDecision) -> dict | None:
        """결정을 실제 주문으로 실행. dry_run이면 주문 안 내고 None 반환."""
        if decision.action == "HOLD" or decision.qty == 0:
            return None
        if self.dry_run:
            print(
                f"[DRY-RUN] {decision.action} {decision.symbol} "
                f"{decision.qty}주 (@시장가, 현재가 {decision.price:,}원) — 실제 주문 안 함"
            )
            return None

        side = "buy" if decision.action == "BUY" else "sell"
        result = self.client.order_cash(
            symbol=decision.symbol,
            qty=decision.qty,
            side=side,
            order_type="01",  # 시장가
        )
        order_no = result.get("output", {}).get("ODNO", "?")
        print(
            f"[주문완료] {decision.action} {decision.symbol} "
            f"{decision.qty}주 — 주문번호 {order_no}"
        )
        return result

    def run_once(self, symbol: str, strategy: Strategy, budget: int) -> TradeDecision:
        """1회 실행: 결정 출력 + (dry_run 아니면) 주문."""
        d = self.decide(symbol, strategy, budget)
        print(
            f"\n[{symbol}] 목표포지션={d.target_position:.0f} "
            f"보유 {d.current_qty}주 → 목표 {d.target_qty}주 "
            f"⇒ {d.action}" + (f" {d.qty}주" if d.qty else "")
        )
        self.execute(d)
        return d
