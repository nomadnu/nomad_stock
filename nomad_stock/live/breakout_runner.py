"""변동성 돌파 장중 실행기.

전략:
  목표가 = 당일 시가 + k * 전일(고가-저가)
  장중 현재가가 목표가를 '처음' 넘으면 시장가 매수(돌파 진입).
  장 마감 직전(exit_time)에 보유분을 전량 시장가 매도(오버나이트 미보유).

전일 변동폭은 FDR 일봉(직전 완성된 거래일)에서, 당일 시가/현재가는 KIS에서 받는다.
종목별 상태(목표가/진입여부)를 들고 폴링 루프를 돈다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ..broker import KISClient
from ..data.loader import load_ohlcv


@dataclass
class BreakoutState:
    symbol: str
    k: float
    budget: int
    target: float | None = None   # 목표가(당일 시가 확정 후 계산)
    entered: bool = False         # 오늘 진입했는지
    qty: int = 0                  # 보유 수량
    done: bool = False            # 오늘 처리 종료(청산까지)
    prev_range: float = field(default=0.0)


class BreakoutLiveRunner:
    def __init__(self, client: KISClient | None = None, dry_run: bool = True):
        self.client = client or KISClient()
        self.dry_run = dry_run

    def _prev_range(self, symbol: str) -> float:
        """직전 완성 거래일의 (고가-저가). 오늘 행이 있으면 제외."""
        df = load_ohlcv(symbol, start="2024-01-01", use_cache=False)
        today = date.today()
        past = df[df.index.date < today]
        if past.empty:
            past = df.iloc[:-1] if len(df) > 1 else df
        last = past.iloc[-1]
        return float(last["High"] - last["Low"])

    def init_state(self, symbol: str, k: float, budget: int) -> BreakoutState:
        st = BreakoutState(symbol=symbol, k=k, budget=budget)
        st.prev_range = self._prev_range(symbol)
        return st

    def _ensure_target(self, st: BreakoutState) -> None:
        """당일 시가가 확정되면 목표가를 계산(최초 1회)."""
        if st.target is not None:
            return
        q = self.client.get_quote(st.symbol)
        if q["open"] > 0:
            st.target = q["open"] + st.k * st.prev_range

    def step(self, st: BreakoutState) -> str:
        """1회 폴링. 진입/대기 상태 문자열을 반환한다."""
        if st.done:
            return "완료"
        self._ensure_target(st)
        if st.target is None:
            return "시가 미확정"

        q = self.client.get_quote(st.symbol)
        price = q["price"]
        if not st.entered:
            if price >= st.target:
                qty = st.budget // price if price > 0 else 0
                if qty > 0:
                    self._order(st.symbol, qty, "buy", price)
                    st.entered, st.qty = True, qty
                    return f"돌파 진입 BUY {qty}주 @ {price:,} (목표가 {st.target:,.0f})"
                return "예산 부족"
            return f"대기 (현재 {price:,} < 목표 {st.target:,.0f})"
        return f"보유 중 {st.qty}주 (현재 {price:,})"

    def close_position(self, st: BreakoutState) -> str:
        """장 마감 직전 청산."""
        if st.entered and st.qty > 0 and not st.done:
            q = self.client.get_quote(st.symbol)
            self._order(st.symbol, st.qty, "sell", q["price"])
            msg = f"청산 SELL {st.qty}주 @ {q['price']:,}"
            st.qty, st.done = 0, True
            return msg
        st.done = True
        return "청산할 보유 없음"

    def _order(self, symbol: str, qty: int, side: str, price: int) -> None:
        if self.dry_run:
            print(f"[DRY-RUN] {side.upper()} {symbol} {qty}주 (현재가 {price:,}) — 실제 주문 안 함")
            return
        result = self.client.order_cash(symbol, qty, side, order_type="01")
        odno = result.get("output", {}).get("ODNO", "?")
        print(f"[주문완료] {side.upper()} {symbol} {qty}주 — 주문번호 {odno}")
