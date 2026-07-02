"""운용 지침서 v0.3의 자금·리스크 기준을 담는 중앙 설정.

모든 매매/리스크 로직이 여기를 참조한다. 상수는 지침서 고정값이고,
운용원금(capital)과 정지상태(halted)만 런타임에 바뀌며 bot_state.json에 저장된다.
  - capital: /원금변경 명령으로만 변경 (예수금 실시간 조회와 별개)
  - halted:  /정지 킬스위치 또는 방어선 도달 시 True → 모든 자동매매 중단
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_PATH = os.path.join(_ROOT, "bot_state.json")

# ===== 지침서 v0.3 고정 기준 =====
DEFAULT_CAPITAL = 10_000_000        # 목표 운용 원금 (한도 계산 기준)
DEFENSE_LINE = 1_000_000            # 누적 손실 방어선 (-100만, 원금의 10%)
MAX_POSITION_PCT = 0.20             # 한 종목 최대 비중
MAX_POSITION_AMOUNT = 2_000_000     # 한 종목 최대 금액 (200만)
STOP_LOSS_PCT = 0.07                # 종목당 손절선 -7%
ATR_STOP_MULT = 2.0                 # ATR×2 손절 (대체 기준)
APPROVAL_TIME = "12:50"             # 승인 알림 시각 (오전장 강세 종목)

# 종목 유니버스 & 자동 필터
UNIVERSE = "KOSPI200"
MIN_MARKET_CAP = 500_000_000_000    # 시가총액 하한 5,000억
MIN_TRADING_VALUE = 5_000_000_000   # 일평균 거래대금 하한 50억
MA_TREND = 60                       # 추세 판단 이동평균 (60일선 위)


@dataclass
class BotState:
    capital: int = DEFAULT_CAPITAL   # 운용 원금 (/원금변경으로만)
    halted: bool = False             # 킬스위치/방어선 정지 여부
    halt_reason: str = ""            # 정지 사유

    def position_budget(self) -> int:
        """한 종목에 넣을 수 있는 최대 금액 = min(원금×20%, 200만)."""
        return min(int(self.capital * MAX_POSITION_PCT), MAX_POSITION_AMOUNT)

    def defense_floor(self) -> int:
        """총평가가 이 값 밑으로 내려가면 방어선 도달 (원금 - 100만)."""
        return self.capital - DEFENSE_LINE


def load_state() -> BotState:
    if os.path.exists(_STATE_PATH):
        try:
            with open(_STATE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return BotState(
                capital=int(data.get("capital", DEFAULT_CAPITAL)),
                halted=bool(data.get("halted", False)),
                halt_reason=str(data.get("halt_reason", "")),
            )
        except (json.JSONDecodeError, ValueError):
            pass
    return BotState()


def save_state(state: BotState) -> None:
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, ensure_ascii=False, indent=2)


def halt(reason: str) -> BotState:
    st = load_state()
    st.halted, st.halt_reason = True, reason
    save_state(st)
    return st


def resume() -> BotState:
    st = load_state()
    st.halted, st.halt_reason = False, ""
    save_state(st)
    return st


def set_capital(amount: int) -> BotState:
    st = load_state()
    st.capital = int(amount)
    save_state(st)
    return st
