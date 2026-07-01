from .base import Strategy
from .bollinger import BollingerStrategy
from .rsi import RsiStrategy
from .sma_cross import SmaCrossStrategy

__all__ = [
    "Strategy",
    "SmaCrossStrategy",
    "RsiStrategy",
    "BollingerStrategy",
    "make_strategy",
]


def make_strategy(name: str, **params) -> Strategy:
    """이름으로 전략 객체를 생성한다 (watchlist.json / CLI에서 사용).

    지원: "sma"(SmaCross), "rsi"(Rsi).
    변동성 돌파(breakout)는 종가기반 신호 인터페이스가 아니라 전용 백테스트를
    쓰므로 여기서 만들지 않는다 (strategy.breakout.run_breakout_backtest 사용).
    """
    name = name.lower()
    if name in ("sma", "sma_cross", "smacross"):
        return SmaCrossStrategy(**params)
    if name in ("rsi",):
        return RsiStrategy(**params)
    if name in ("bb", "bollinger"):
        return BollingerStrategy(**params)
    raise ValueError(f"알 수 없는 전략: {name} (지원: sma, rsi, bb)")
