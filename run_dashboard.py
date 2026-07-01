"""웹 대시보드 실행.

브라우저에서 잔고·보유종목·손익·전략신호·매매로그를 본다.

사용법:
    python run_dashboard.py            # http://127.0.0.1:5000 (이 PC에서만, 개발서버)
    python run_dashboard.py --lan      # 같은 와이파이의 폰에서도 접속 (0.0.0.0)
    python run_dashboard.py --prod     # 프로덕션 서버(waitress)로 0.0.0.0 바인딩 (클라우드용)

외부(회사)에서 보려면 클라우드 배포 + DASHBOARD_PASSWORD 설정이 필요하다.
인증 없이(비밀번호 미설정) 외부 바인딩하면 경고한다.
"""
from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nomad_stock.web import create_app


def main() -> None:
    prod = "--prod" in sys.argv
    lan = "--lan" in sys.argv
    external = prod or lan
    host = "0.0.0.0" if external else "127.0.0.1"
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    has_pw = bool(os.getenv("DASHBOARD_PASSWORD", "").strip())

    if external and not has_pw:
        print("⚠️  외부 바인딩인데 DASHBOARD_PASSWORD가 없습니다 — 누구나 계좌를 봅니다!")
        print("    .env에 DASHBOARD_PASSWORD를 설정하거나 localhost로만 쓰세요.")

    app = create_app()
    print(f"대시보드: http://127.0.0.1:{port}  로그인:{'켜짐' if has_pw else '꺼짐'}  (Ctrl+C 종료)")
    if external:
        print(f"외부 접속: http://<서버IP>:{port}")

    if prod:
        from waitress import serve

        serve(app, host=host, port=port, threads=8)
    else:
        app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
