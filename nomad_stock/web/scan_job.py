"""스캔을 별도 프로세스로 실행 (웹 프로세스가 스캔에 밀리지 않게).

무거운 스캔(미국 yfinance 60종목 등)을 대시보드와 같은 프로세스에서 돌리면
1GB 서버에서 웹 응답이 굶어(조회실패) → 별도 프로세스로 분리.

사용: python -m nomad_stock.web.scan_job US|KR|BOTH
"""
from __future__ import annotations

import sys

from ..broker import KISClient
from . import actions


def main() -> None:
    market = sys.argv[1] if len(sys.argv) > 1 else "BOTH"
    client = KISClient()
    if market in ("KR", "BOTH"):
        try:
            actions.run_scan("KR", client)
        except Exception as e:
            print(f"[scan_job] KR 오류: {e!r}")
    if market in ("US", "BOTH"):
        try:
            actions.run_scan("US", client)
        except Exception as e:
            print(f"[scan_job] US 오류: {e!r}")
    print(f"[scan_job] 완료: {market}")


if __name__ == "__main__":
    main()
