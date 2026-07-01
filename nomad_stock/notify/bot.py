"""텔레그램 양방향 조회 봇.

폰에서 봇에게 명령을 보내면 계좌 현황을 답장한다. 롱폴링(getUpdates) 방식이라
서버·웹훅 설정 없이 상주 실행만 하면 된다(집 PC에서 돌리면 회사에서도 조회 가능).

인증: .env의 TELEGRAM_CHAT_ID 본인만 응답한다(다른 사람이 봇을 알아도 차단).

명령: 잔고 / 손익 / 오늘 / 신호 / 도움말 (슬래시 /balance 등도 지원)
"""
from __future__ import annotations

import json
import os
from datetime import date

import requests

from ..broker import KISClient
from ..live.market_hours import market_status
from ..strategy import make_strategy

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_API = "https://api.telegram.org/bot{token}/{method}"


def _won(n) -> str:
    try:
        return f"{int(n):,}원"
    except (TypeError, ValueError):
        return "-"


class TradingBot:
    def __init__(self, client: KISClient | None = None):
        from dotenv import load_dotenv

        load_dotenv()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (.env) 필요")
        self.client = client or KISClient()

    # ----- 텔레그램 API -----
    def _call(self, method: str, **params):
        url = _API.format(token=self.token, method=method)
        return requests.post(url, json=params, timeout=40)

    def send(self, text: str, chat_id: str | None = None) -> None:
        self._call("sendMessage", chat_id=chat_id or self.chat_id, text=text)

    # ----- 명령 처리 -----
    def handle(self, text: str) -> str:
        t = text.strip().lower().lstrip("/")
        if t in ("잔고", "잔액", "balance", "start"):
            return self.balance_text()
        if t in ("손익", "수익", "pnl"):
            return self.pnl_text()
        if t in ("오늘", "오늘매매", "today"):
            return self.today_text()
        if t in ("신호", "signal"):
            return self.signal_text()
        if t in ("도움말", "명령", "help", "?"):
            return self.help_text()
        return "모르는 명령이에요.\n" + self.help_text()

    def help_text(self) -> str:
        return (
            "📖 명령 목록\n"
            "• 잔고 — 예수금·보유종목·손익\n"
            "• 손익 — 평가손익 요약\n"
            "• 오늘 — 오늘 매매 내역\n"
            "• 신호 — 전략 현재 신호\n"
            "• 도움말 — 이 안내"
        )

    def balance_text(self) -> str:
        bal = self.client.get_balance()
        lines = [
            f"💰 계좌 현황 ({market_status()})",
            f"예수금 {_won(bal['cash'])}",
            f"총평가 {_won(bal['total_eval'])}",
        ]
        if bal["holdings"]:
            lines.append("\n보유종목:")
            for h in bal["holdings"]:
                pct = (h["cur_price"] / h["avg_price"] - 1) * 100 if h["avg_price"] else 0
                lines.append(
                    f"• {h['name']} {h['qty']}주\n"
                    f"  {int(h['avg_price']):,}→{h['cur_price']:,} "
                    f"({pct:+.1f}%, {h['eval_pnl']:+,}원)"
                )
        else:
            lines.append("\n보유종목 없음")
        return "\n".join(lines)

    def pnl_text(self) -> str:
        bal = self.client.get_balance()
        total = sum(h["eval_pnl"] for h in bal["holdings"])
        emoji = "🔴" if total > 0 else ("🔵" if total < 0 else "⚪")
        lines = [f"{emoji} 평가손익 합계: {total:+,}원"]
        for h in bal["holdings"]:
            pct = (h["cur_price"] / h["avg_price"] - 1) * 100 if h["avg_price"] else 0
            lines.append(f"• {h['name']}: {h['eval_pnl']:+,}원 ({pct:+.1f}%)")
        return "\n".join(lines) if bal["holdings"] else "보유종목이 없어 손익이 없습니다."

    def today_text(self) -> str:
        path = os.path.join(_ROOT, "logs", "trades.log")
        if not os.path.exists(path):
            return "오늘 매매 기록이 없습니다."
        today = date.today().isoformat()
        with open(path, encoding="utf-8") as f:
            todays = [
                l.strip() for l in f
                if l.startswith(today) and any(
                    k in l for k in ("주문완료", "BUY", "SELL", "⇒", "손절", "익절")
                )
            ]
        if not todays:
            return f"오늘({today}) 매매 내역이 없습니다."
        return f"📅 오늘 매매 ({today})\n" + "\n".join(todays[-15:])

    def signal_text(self) -> str:
        path = os.path.join(_ROOT, "watchlist.json")
        if not os.path.exists(path):
            return "watchlist.json이 없습니다."
        from ..data.loader import load_ohlcv

        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        lines = ["📊 전략 신호"]
        for it in cfg.get("items", []):
            try:
                df = load_ohlcv(it["symbol"], start="2023-01-01", use_cache=False)
                strat = make_strategy(it.get("strategy", "sma"), **it.get("params", {}))
                sig = float(strat.generate_signals(df).iloc[-1])
                lines.append(f"• {it['symbol']} {strat.name}: {'🟢매수보유' if sig >= 1 else '⚪현금'}")
            except Exception as e:
                lines.append(f"• {it['symbol']}: 오류({e})")
        return "\n".join(lines)

    # ----- 롱폴링 루프 -----
    def run(self) -> None:
        print(f"[봇] 시작 — 인증 chat_id={self.chat_id}. Ctrl+C로 종료.")
        self.send("🤖 조회 봇이 켜졌어요. '도움말' 을 보내보세요.")
        offset = None
        while True:
            try:
                resp = self._call("getUpdates", offset=offset, timeout=30)
                data = resp.json()
            except Exception as e:
                print(f"[봇] 폴링 오류: {e!r}")
                continue
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}
                chat = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if not text:
                    continue
                if chat != self.chat_id:  # 본인만 응답
                    continue
                try:
                    reply = self.handle(text)
                except Exception as e:
                    reply = f"조회 중 오류: {e}"
                self.send(reply, chat_id=chat)
