"""KIS 연결 테스트 (읽기 전용 - 주문 안 함).

.env 설정 후 실행:
    python test_kis.py            # 삼성전자 현재가 + 잔고 조회
    python test_kis.py 000660     # SK하이닉스
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.broker import KISClient


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "005930"

    client = KISClient()
    print(f"환경: {client.cfg.env}  (paper=모의투자, real=실거래)")
    print(f"계좌: {client.cfg.cano}-{client.cfg.acnt_prdt_cd}")

    print("\n[1] 접근토큰 발급...")
    token = client.token()
    print(f"    OK (토큰 길이 {len(token)})")

    print(f"\n[2] {symbol} 현재가 조회...")
    price = client.get_price(symbol)
    print(f"    현재가: {price:,}원")

    print("\n[3] 계좌 잔고 조회...")
    bal = client.get_balance()
    print(f"    예수금: {bal['cash']:,}원 / 총평가: {bal['total_eval']:,}원")
    if bal["holdings"]:
        print("    보유종목:")
        for h in bal["holdings"]:
            print(
                f"      - {h['name']}({h['symbol']}) {h['qty']}주 "
                f"@ {h['avg_price']:,.0f} → {h['cur_price']:,} "
                f"(평가손익 {h['eval_pnl']:,}원)"
            )
    else:
        print("    보유종목 없음")

    print("\n연결 정상. 주문 테스트는 run_live.py로 진행하세요.")


if __name__ == "__main__":
    main()
