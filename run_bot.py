"""텔레그램 조회 봇 실행.

폰에서 봇에게 '잔고/손익/오늘/신호'를 보내면 답한다. 상주 실행.

사용법:
    python run_bot.py

집 PC에서 켜두면(자동매매와 함께) 회사·외부에서도 폰으로 조회할 수 있다.
"""
from __future__ import annotations

import os
import sys

# 항상 logs/bot.log 에 기록 (pythonw는 stdout이 없고, 백그라운드라 콘솔도 안 보이므로).
# 줄 단위 버퍼링(buffering=1)이라 프로세스가 죽어도 로그가 남는다.
_logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_logdir, exist_ok=True)
_f = open(os.path.join(_logdir, "bot.log"), "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = _f

from nomad_stock.notify.bot import TradingBot


def main() -> None:
    bot = TradingBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n[봇] 종료됨.")


if __name__ == "__main__":
    main()
