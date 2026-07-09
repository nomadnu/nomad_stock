"""텔레그램 양방향 조회 봇.

폰에서 봇에게 명령을 보내면 계좌 현황을 답장한다. 롱폴링(getUpdates) 방식이라
서버·웹훅 설정 없이 상주 실행만 하면 된다(집 PC에서 돌리면 회사에서도 조회 가능).

인증: .env의 TELEGRAM_CHAT_ID 본인만 응답한다(다른 사람이 봇을 알아도 차단).

명령: 잔고 / 손익 / 오늘 / 신호 / 도움말 (슬래시 /balance 등도 지원)
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import requests

from .. import paper_long, paper_track_d, paper_us, rules
from ..broker import KISClient
from ..live.market_hours import is_market_open, market_status
from ..live.risk import RiskConfig, RiskManager
from ..rebalance import SELL as _REVIEW_SELL, review_holdings
from ..scanner import format_candidates, format_us_candidates, scan, scan_us
from ..strategy import make_strategy

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_API = "https://api.telegram.org/bot{token}/{method}"
_PENDING_PATH = os.path.join(_ROOT, "pending.json")


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
        parts = text.strip().split()
        cmd = parts[0].lower().lstrip("/") if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("잔고", "잔액", "balance"):
            return self.balance_text()
        if cmd in ("손익", "수익", "pnl"):
            return self.pnl_text()
        if cmd in ("오늘", "오늘매매", "today"):
            return self.today_text()
        if cmd in ("신호", "signal"):
            return self.signal_text()
        if cmd in ("상태", "status"):
            return self.status_text()
        if cmd in ("정지", "stop", "kill"):
            return self.stop_text()
        if cmd in ("재개", "resume"):
            return self.resume_text()
        if cmd in ("현재가", "price"):
            return self.price_text(arg)
        if cmd in ("원금변경", "setcapital"):
            return self.setcapital_text(arg)
        if cmd in ("재평가", "점검", "review"):
            self.run_weekly_review()
            return "📊 주간 재평가를 실행했어요 (위 결과 확인)."
        if cmd in ("미국", "미장", "us"):
            self.run_us_paper_alert()
            return "🇺🇸 미국 페이퍼 알림을 실행했어요 (위 결과 확인)."
        if cmd in ("미국잔고", "미국계좌", "usbalance"):
            return paper_us.format_balance()
        if cmd in ("역추세", "역추세잔고", "d"):
            self.run_d_alert()
            return "🔄 역추세 알림을 실행했어요 (위 결과 확인)."
        if cmd in ("역추세계좌", "d잔고", "dbalance"):
            return paper_track_d.format_balance()
        if cmd in ("장기", "장기편입", "long"):
            self.run_long_alert()
            return "📗 장기 편입 후보를 스캔했어요 (위 결과)."
        if cmd in ("장기잔고", "장기계좌"):
            return paper_long.format_balance()
        if cmd in ("장기점검", "분기점검"):
            self.run_long_review()
            return "📗 장기 분기 점검을 실행했어요 (위 결과)."
        if cmd in ("도움말", "명령", "help", "?", "start"):
            return self.help_text()
        return "모르는 명령이에요.\n" + self.help_text()

    def help_text(self) -> str:
        return (
            "📖 명령 목록\n"
            "• 잔고 — 예수금·보유종목·손익\n"
            "• 손익 — 평가손익 요약\n"
            "• 현재가 [종목코드] — 예: 현재가 005930\n"
            "• 오늘 — 오늘 매매 내역\n"
            "• 신호 — 전략 현재 신호\n"
            "• 상태 — 봇 가동/정지·한도·방어선\n"
            "• 재평가 — 보유종목 주간 점검 (수동 실행)\n"
            "• 미국 — 미국 페이퍼 후보 알림 (매수/매도 승인)\n"
            "• 미국잔고 — 미국 페이퍼 계좌 (달러·원화)\n"
            "• 장기 — 미국 장기 3박자 편입 후보\n"
            "• 장기잔고 — 장기 계좌 (S&P500 대비 초과수익)\n"
            "• 정지 / 재개 — 자동매매 킬스위치\n"
            "• 원금변경 [금액] — 운용원금 변경\n"
            "• myid — 내 chat_id 확인\n"
            "• 도움말 — 이 안내"
        )

    def status_text(self) -> str:
        st = rules.load_state()
        lines = [f"⚙️ 봇 상태 ({market_status()})"]
        lines.append("🟢 가동 중" if not st.halted else f"🔴 정지됨 — {st.halt_reason}")
        lines.append(f"운용원금 {st.capital:,}원")
        lines.append(f"한 종목 한도 {st.position_budget():,}원 (원금 20%/최대 200만)")
        lines.append(
            f"손절선 -{rules.STOP_LOSS_PCT * 100:.0f}% · 방어선 -{rules.DEFENSE_LINE:,}원"
        )
        try:
            bal = self.client.get_balance()
            loss = st.capital - bal["total_eval"]
            lines.append(f"총평가 {bal['total_eval']:,}원 (원금대비 {-loss:+,}원)")
            if loss >= rules.DEFENSE_LINE:
                lines.append("⚠️ 방어선 도달! 자동매매가 정지되어야 합니다.")
        except Exception:
            pass
        return "\n".join(lines)

    def stop_text(self) -> str:
        rules.halt("수동 정지 (/정지)")
        return "🛑 자동매매를 정지했습니다. '재개' 로 다시 켤 수 있어요."

    def resume_text(self) -> str:
        rules.resume()
        return "🟢 자동매매를 재개했습니다."

    def price_text(self, arg: str) -> str:
        if not (arg.isdigit() and len(arg) == 6):
            return "종목코드 6자리를 붙여주세요. 예: 현재가 005930"
        try:
            price = self.client.get_price(arg)
            return f"{arg} 현재가: {price:,}원"
        except Exception as e:
            return f"조회 실패: {e}"

    def setcapital_text(self, arg: str) -> str:
        digits = arg.replace(",", "").replace("원", "")
        if not digits.isdigit():
            return "금액을 숫자로 주세요. 예: 원금변경 5000000"
        st = rules.set_capital(int(digits))
        return (
            f"운용원금을 {st.capital:,}원으로 변경했습니다.\n"
            f"한 종목 한도: {st.position_budget():,}원"
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

    # ----- 승인 흐름 (STEP 2) -----
    def send_buttons(self, text: str, buttons: list[dict]) -> None:
        """인라인 버튼과 함께 메시지 전송. buttons: [{text, callback_data}] (한 줄에 하나)."""
        keyboard = {"inline_keyboard": [[b] for b in buttons]}
        self._call("sendMessage", chat_id=self.chat_id, text=text, reply_markup=keyboard)

    def _save_pending(self, cands: list[dict]) -> None:
        with open(_PENDING_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": date.today().isoformat(),
                       "codes": [c["code"] for c in cands]}, f)

    def _pending_codes(self) -> set[str]:
        if not os.path.exists(_PENDING_PATH):
            return set()
        try:
            with open(_PENDING_PATH, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("date") == date.today().isoformat():
                return set(d.get("codes", []))
        except (json.JSONDecodeError, KeyError):
            pass
        return set()

    def run_daily_scan(self) -> None:
        """12:50 실행: 강세 종목 스캔 → 승인 버튼 알림. 정지 상태면 스캔 안 함."""
        st = rules.load_state()
        if st.halted:
            self.send(f"🔴 정지 상태({st.halt_reason})라 오늘 스캔을 건너뜁니다.")
            return
        cands = scan(self.client)
        self._save_pending(cands)
        header = "🌎 미국장 요약: (v1 미제공)\n\n"
        text = header + format_candidates(cands)
        if not cands:
            self.send(text)
            return
        buttons = [
            {"text": f"✅ 승인 {c['name']}", "callback_data": f"buy:{c['code']}"}
            for c in cands
        ]
        buttons.append({"text": "❌ 전체 무시", "callback_data": "skip"})
        self.send_buttons(text, buttons)

    def execute_buy(self, code: str) -> str:
        """승인된 종목을 한 종목 한도(원금 20%/최대 200만) 내에서 시장가 매수."""
        st = rules.load_state()
        if st.halted:
            return f"🔴 정지 상태({st.halt_reason})라 매수 불가. '재개' 후 가능."
        try:
            price = self.client.get_price(code)
        except Exception as e:
            return f"현재가 조회 실패: {e}"
        budget = st.position_budget()
        qty = budget // price if price > 0 else 0
        if qty < 1:
            return f"{code}: 한도({budget:,}원)로 1주도 못 삽니다 (현재가 {price:,})."
        try:
            result = self.client.order_cash(code, qty, "buy", order_type="01")
            odno = result.get("output", {}).get("ODNO", "?")
            return (
                f"✅ 매수 완료: {code} {qty}주 @시장가 "
                f"(현재가 {price:,}, 약 {qty * price:,}원) 주문번호 {odno}"
            )
        except Exception as e:
            return f"주문 실패: {e}"

    # ----- 미국 페이퍼 트레이딩 (트랙 B, 지침서 v0.5 / 실제주문 없음) -----
    def run_us_paper_alert(self) -> None:
        """밤 9시: 미국 매수후보 + 보유 매도후보를 승인 버튼과 함께. 승인분은 개장後 기록 예약."""
        st = rules.load_state()
        if st.halted:
            self.send("🔴 정지 상태라 미국 페이퍼 알림을 건너뜁니다.")
            return
        self.send("🇺🇸 미국 페이퍼 후보 스캔 중... (1~2분)")
        try:
            buys = scan_us()
            fx = paper_us.fx_rate()
        except Exception as e:
            self.send(f"미국 스캔 실패: {e}")
            return
        led = paper_us.load_ledger()
        sells = []
        for sym, pos in led["positions"].items():
            status, reason = paper_us.evaluate_us_holding(sym, pos["avg_usd"])
            if status == "매도검토":
                sells.append((sym, pos["name"], reason))

        lines = [f"🇺🇸 미국 페이퍼 트레이딩 (밤 9시 · 환율 {fx:.0f})"]
        lines.append("\n[매수 후보] (PER 대신 모멘텀순)" if buys else "\n매수 후보 없음")
        for c in buys:
            lines.append(
                f"• {c['name']}({c['symbol']}) ${c['price']} (≈{c['price']*fx:,.0f}원)"
                f" · 5일 {c['ret5']:+.1f}% · 1개월 {c['ret20']:+.1f}%"
            )
        if sells:
            lines.append("\n[매도 후보(보유중)]")
            for sym, name, reason in sells:
                lines.append(f"• {name}({sym}): {reason}")
        lines.append("\n승인분은 새벽 개장 후 실시간 가격으로 장부에 기록돼요 (지금 안 주무셔도 됨).")

        buttons = [{"text": f"✅매수 {c['name'][:12]}", "callback_data": f"usbuy:{c['symbol']}"} for c in buys]
        buttons += [{"text": f"🔴매도 {name[:12]}", "callback_data": f"ussell:{sym}"} for sym, name, _ in sells]
        if buttons:
            buttons.append({"text": "❌ 무시", "callback_data": "usignore"})
            self.send_buttons("\n".join(lines), buttons)
        else:
            self.send("\n".join(lines) + "\n\n오늘은 후보 없음. 현금 대기.")

    def run_us_paper_settle(self) -> None:
        """개장+3h20m: 예약분을 실시간 가격으로 장부 기록 + 손절(-7%) 자동 기록."""
        resv = paper_us.pop_reservations()
        try:
            fx = paper_us.fx_rate()
        except Exception:
            fx = 0
        results = []
        for r in resv:
            try:
                price = paper_us.us_price(r["symbol"])
            except Exception:
                results.append(f"{r['symbol']} 가격조회 실패")
                continue
            if r["action"] == "buy":
                results.append(paper_us.record_buy(r["symbol"], r["name"], price, fx)["msg"])
            else:
                results.append(paper_us.record_sell(r["symbol"], price, fx, "매도")["msg"])
        # 손절 자동(-7%)
        led = paper_us.load_ledger()
        for sym, pos in list(led["positions"].items()):
            try:
                cur = paper_us.us_price(sym)
            except Exception:
                continue
            if pos["avg_usd"] and (cur / pos["avg_usd"] - 1) <= -rules.STOP_LOSS_PCT:
                results.append(paper_us.record_sell(sym, cur, fx, "손절")["msg"])
        if results:
            self.send("🇺🇸 미국 페이퍼 기록 (개장 후 실시간가):\n" + "\n".join(results))

    # ----- 미국 장기투자 (트랙 C, 3박자 필터 / 손절 없음 / 별도 장부) -----
    def run_long_alert(self) -> None:
        """3박자 통과 장기 편입 후보 → 편입 승인 버튼."""
        self.send("📗 장기 3박자 필터 스캔 중... (재무지표 조회, 1~2분)")
        try:
            from ..scanner_long import format_long_candidates, scan_long
            cands = scan_long()
        except Exception as e:
            self.send(f"장기 스캔 실패: {e}")
            return
        text = format_long_candidates(cands)
        if cands:
            buttons = [{"text": f"📗편입 {c['name'][:12]}", "callback_data": f"longbuy:{c['symbol']}"} for c in cands]
            buttons.append({"text": "❌ 보류", "callback_data": "longignore"})
            self.send_buttons(text, buttons)
        else:
            self.send(text)

    # ----- 트랙 D 역추세 (밤9시 추천 → 개장 후 체결, 손절 자동) -----
    def run_d_alert(self) -> None:
        """밤 9시: 역추세 매수후보(물타기 금지) + 보유 익절/손절 후보. 개장 후 체결 예약."""
        st = rules.load_state()
        if st.halted:
            return
        from ..scanner import evaluate_d_holding, scan_d
        try:
            buys = scan_d(exclude=paper_track_d.held_symbols())
            fx = paper_us.fx_rate()
        except Exception as e:
            self.send(f"역추세 스캔 실패: {e}")
            return
        led = paper_track_d.load_ledger()
        sells = []
        for sym, pos in led["positions"].items():
            status, reason = evaluate_d_holding(sym, pos["avg_usd"])
            if status in ("익절검토", "손절"):
                sells.append((sym, pos["name"], f"{status}: {reason}"))
        lines = [f"🔄 역추세(트랙D) 페이퍼 (밤 9시 · 환율 {fx:.0f})"]
        lines.append("\n[매수 후보] 볼린저 하단+과매도 (RSI 낮은순)" if buys else "\n매수 후보 없음")
        for c in buys:
            lines.append(f"• {c['name']}({c['symbol']}) ${c['price']} · RSI {c['rsi']} (하단 ${c['lower']})")
        if sells:
            lines.append("\n[매도 후보(보유중)]")
            for sym, name, reason in sells:
                lines.append(f"• {name}({sym}): {reason}")
        lines.append("\n승인분은 새벽 개장 후 실시간가로 기록. 손절 -7%는 자동.")
        buttons = [{"text": f"✅매수 {c['name'][:12]}", "callback_data": f"dbuy:{c['symbol']}"} for c in buys]
        buttons += [{"text": f"🔴매도 {name[:12]}", "callback_data": f"dsell:{sym}"} for sym, name, _ in sells]
        if buttons:
            buttons.append({"text": "❌ 무시", "callback_data": "dignore"})
            self.send_buttons("\n".join(lines), buttons)
        else:
            self.send("\n".join(lines) + "\n\n오늘은 후보 없음. 현금 대기.")

    def run_d_settle(self) -> None:
        """역추세 예약을 개장 후 실시간가로 기록 + 손절(-7%) 자동."""
        resv = paper_track_d.pop_reservations()
        try:
            fx = paper_us.fx_rate()
        except Exception:
            fx = 0
        results = []
        for r in resv:
            try:
                price = paper_us.us_price(r["symbol"])
            except Exception:
                results.append(f"{r['symbol']} 가격조회 실패")
                continue
            if r["action"] == "buy":
                results.append(paper_track_d.record_buy(r["symbol"], r["name"], price, fx)["msg"])
            else:
                results.append(paper_track_d.record_sell(r["symbol"], price, fx, "매도")["msg"])
        # 손절 자동(-7%)
        led = paper_track_d.load_ledger()
        for sym, pos in list(led["positions"].items()):
            try:
                cur = paper_us.us_price(sym)
            except Exception:
                continue
            if pos["avg_usd"] and (cur / pos["avg_usd"] - 1) <= -rules.STOP_LOSS_PCT:
                results.append(paper_track_d.record_sell(sym, cur, fx, "손절")["msg"])
        if results:
            self.send("🔄 역추세 기록 (개장 후 실시간가):\n" + "\n".join(results))

    def run_long_settle(self) -> None:
        """장기 편입/매도 예약을 개장 후 실시간 가격으로 기록 (트랙B와 동일 시각)."""
        resv = paper_long.pop_reservations()
        if not resv:
            return
        try:
            fx = paper_us.fx_rate()
        except Exception:
            fx = 0
        results = []
        for r in resv:
            try:
                price = paper_us.us_price(r["symbol"])
            except Exception:
                results.append(f"{r['symbol']} 가격조회 실패")
                continue
            if r["action"] == "buy":
                results.append(paper_long.record_buy(r["symbol"], r["name"], price, fx)["msg"])
            else:
                results.append(paper_long.record_sell(r["symbol"], price, fx)["msg"])
        if results:
            self.send("📗 장기 기록 (개장 후 실시간가):\n" + "\n".join(results))

    def run_long_review(self) -> None:
        """분기 점검: 보유 종목의 3박자(편입 근거)를 재확인 → 훼손 시 매도 검토."""
        led = paper_long.load_ledger()
        if not led["positions"]:
            self.send("📗 장기 분기 점검: 보유 종목 없음.")
            return
        import yfinance as yf

        from ..scanner_long import pass_3factor
        lines = ["📗 장기 분기 점검 (편입 근거 유효성)"]
        sells = []
        for sym, pos in led["positions"].items():
            try:
                ok, m = pass_3factor(yf.Ticker(sym).info)
            except Exception:
                ok, m = True, {}
            if ok:
                lines.append(f"• {pos['name']}({sym}): 근거 유지 ✅")
            else:
                broken = [x for x, k in [("성장", "g_ok"), ("재무", "f_ok"), ("밸류", "v_ok")]
                          if not m.get(k, True)]
                lines.append(f"• {pos['name']}({sym}): {'·'.join(broken)} 훼손 ⚠️ → 매도 검토")
                sells.append((sym, pos["name"]))
        text = "\n".join(lines) + "\n\n※ 주가 하락이 아니라 '근거 훼손'만 매도 사유예요."
        if sells:
            buttons = [{"text": f"🔴매도 {name[:12]}", "callback_data": f"longsell:{sym}"} for sym, name in sells]
            buttons.append({"text": "❌ 전체 보유", "callback_data": "longhold"})
            self.send_buttons(text, buttons)
        else:
            self.send(text + "\n\n모두 근거 유지 — 계속 보유 권장.")

    # ----- 주간 재평가 (STEP 7, 지침서 v0.4 §3-2) -----
    def run_weekly_review(self) -> None:
        """금요일 마감 후: 보유종목 재평가 → 매도검토 종목 승인 매도 알림."""
        try:
            bal = self.client.get_balance()
        except Exception as e:
            self.send(f"주간 점검 조회 실패: {e}")
            return
        holdings = bal["holdings"]
        if not holdings:
            self.send("📊 주간 점검: 보유 종목이 없습니다. 현금 대기 중.")
            return
        reviewed = review_holdings(holdings)
        emoji = {"보유권장": "✅", "주의": "🟡", "매도검토": "⚠️"}
        lines = ["📊 보유 종목 주간 점검 (금요일 마감 후)"]
        sell_list = []
        for r in reviewed:
            pnl = (r["cur_price"] / r["avg_price"] - 1) * 100 if r["avg_price"] else 0
            lines.append(
                f"• {r['name']} ({pnl:+.1f}%): {r['reason']} "
                f"→ {r['status']} {emoji.get(r['status'], '')}"
            )
            if r["status"] == _REVIEW_SELL:
                sell_list.append(r)
        text = "\n".join(lines)
        if sell_list:
            buttons = [
                {"text": f"✅ 매도 {r['name']}", "callback_data": f"sell:{r['symbol']}"}
                for r in sell_list
            ]
            buttons.append({"text": "❌ 전체 보유", "callback_data": "hold"})
            self.send_buttons(text + "\n\n매도 검토 종목을 정리할까요?", buttons)
        else:
            self.send(text + "\n\n모두 보유 권장. 조치 불필요.")

    def execute_sell(self, code: str) -> str:
        """승인된 종목을 전량 시장가 매도 (주간 재평가용)."""
        try:
            bal = self.client.get_balance()
        except Exception as e:
            return f"잔고 조회 실패: {e}"
        h = next((x for x in bal["holdings"] if x["symbol"] == code), None)
        if not h:
            return f"{code}: 보유하고 있지 않아요 (이미 정리됨?)."
        try:
            result = self.client.order_cash(code, h["qty"], "sell", order_type="01")
            odno = result.get("output", {}).get("ODNO", "?")
            return f"✅ 매도 완료: {h['name']} {h['qty']}주 (주문번호 {odno})"
        except Exception as e:
            return f"매도 실패: {e}"

    # ----- 리스크 감시 (STEP 5) -----
    def run_risk_check(self) -> None:
        """장중 주기 실행: 방어선 도달 시 완전정지, 종목별 -7% 손절 자동매도.

        정지(halted) 상태면 사용자가 수동 통제 중이므로 손절도 건너뛴다.
        """
        st = rules.load_state()
        if st.halted:
            return
        try:
            bal = self.client.get_balance()
        except Exception:
            return
        # 1) 누적 손실 방어선 (-100만) → 완전 정지 + 긴급 알림
        loss = st.capital - bal["total_eval"]
        if not st.halted and loss >= rules.DEFENSE_LINE:
            rules.halt("누적 손실 방어선(-100만) 도달")
            self.send(
                f"🚨 방어선 도달! 누적손실 {loss:,}원 (총평가 {bal['total_eval']:,}).\n"
                f"자동매매를 완전 정지했습니다. 점검 후 '재개' 하세요."
            )
            return
        # 2) 종목별 손절 (-7%) 자동매도
        rm = RiskManager(
            config=RiskConfig(stop_loss=rules.STOP_LOSS_PCT, take_profit=0.0),
            client=self.client,
        )
        for hit in rm.evaluate(bal["holdings"]):
            try:
                rm.liquidate(hit, dry_run=False)
                self.send(
                    f"🛑 [손절] {hit.symbol} {hit.qty}주 자동매도 "
                    f"(손익 {hit.pnl_pct * 100:+.1f}%)"
                )
            except Exception as e:
                self.send(f"⚠️ {hit.symbol} 손절 주문 실패: {e}")

    # ----- 롱폴링 루프 + 시간 트리거 -----
    def run(self) -> None:
        print(f"[봇] 시작 — 인증 chat_id={self.chat_id}. Ctrl+C로 종료.")
        self.send("🤖 봇 가동. 명령: 상태·잔고·정지. 매일 12:50 강세종목 승인 알림.")
        h, m = rules.APPROVAL_TIME.split(":")
        scan_time = dtime(int(h), int(m))
        rh, rm = rules.REVIEW_TIME.split(":")
        review_time = dtime(int(rh), int(rm))
        uh, um = rules.US_RECOMMEND_TIME.split(":")
        us_time = dtime(int(uh), int(um))
        lh, lm = rules.LONG_ALERT_TIME.split(":")
        long_time = dtime(int(lh), int(lm))
        et_zone = ZoneInfo("America/New_York")
        settle_et = dtime(12, 50)  # 미국 개장 09:30 ET + 3h20m = 12:50 ET (서머타임 자동)
        offset = None
        last_scan_date = None
        last_review_date = None
        last_us_date = None
        last_settle_date = None
        last_long_date = None
        last_quarter = None
        last_risk = 0.0
        while True:
            now = datetime.now()
            # 12:50 일일 스캔 (평일 1회)
            if (now.weekday() < 5 and now.time() >= scan_time
                    and last_scan_date != now.date()):
                last_scan_date = now.date()
                try:
                    self.run_daily_scan()
                except Exception as e:
                    print(f"[봇] 스캔 오류: {e!r}")
            # 금요일 마감 후 주간 재평가 (주 1회)
            if (now.weekday() == rules.REVIEW_DAY and now.time() >= review_time
                    and last_review_date != now.date()):
                last_review_date = now.date()
                try:
                    self.run_weekly_review()
                except Exception as e:
                    print(f"[봇] 재평가 오류: {e!r}")
            # 밤 9시 미국 페이퍼 승인 알림 (KST, 평일 1회)
            if (now.weekday() < 5 and now.time() >= us_time
                    and last_us_date != now.date()):
                last_us_date = now.date()
                try:
                    self.run_us_paper_alert()
                    self.run_d_alert()   # 트랙D 역추세도 밤9시
                except Exception as e:
                    print(f"[봇] 미국 알림 오류: {e!r}")
            # 미국 개장+3h20m(12:50 ET) 예약 체결 기록 (미국 거래일 1회)
            now_et = datetime.now(et_zone)
            if (now_et.weekday() < 5 and now_et.time() >= settle_et
                    and last_settle_date != now_et.date()):
                last_settle_date = now_et.date()
                try:
                    self.run_us_paper_settle()
                    self.run_long_settle()   # 트랙C도 같은 시각(개장 후) 체결
                    self.run_d_settle()      # 트랙D 역추세도 같은 시각
                except Exception as e:
                    print(f"[봇] 미국 체결 오류: {e!r}")
            # 트랙C 장기 편입 알림 (매주 목요일 밤 9시, 주 1회)
            if (now.weekday() == rules.LONG_ALERT_DAY and now.time() >= long_time
                    and last_long_date != now.date()):
                last_long_date = now.date()
                try:
                    self.run_long_alert()
                except Exception as e:
                    print(f"[봇] 장기 알림 오류: {e!r}")
            # 트랙C 장기 분기 점검 (1·4·7·10월 초 1회)
            qkey = f"{now.year}Q{(now.month - 1) // 3 + 1}"
            if (now.month in (1, 4, 7, 10) and now.day <= 3
                    and now.time() >= dtime(9, 0) and last_quarter != qkey):
                last_quarter = qkey
                try:
                    self.run_long_review()
                except Exception as e:
                    print(f"[봇] 장기 점검 오류: {e!r}")
            # 장중 리스크 감시 (3분마다)
            if is_market_open(now) and time.time() - last_risk > 180:
                last_risk = time.time()
                try:
                    self.run_risk_check()
                except Exception as e:
                    print(f"[봇] 리스크 오류: {e!r}")
            # 텔레그램 폴링 (명령 + 버튼 콜백)
            try:
                resp = self._call("getUpdates", offset=offset, timeout=30)
                data = resp.json()
            except Exception as e:
                print(f"[봇] 폴링 오류: {e!r}")
                continue
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    self._handle_callback(u["callback_query"])
                    continue
                msg = u.get("message") or u.get("edited_message") or {}
                chat = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if not text:
                    continue
                print(f"[봇] 수신 chat_id={chat} text={text!r}")
                cmd = text.strip().lower().lstrip("/").split()[0] if text.strip() else ""
                if cmd in ("myid", "아이디"):  # 누구나 자기 chat_id 확인
                    tag = "✅ 등록된 사용자" if chat == self.chat_id else "❌ 미등록 (명령 무시됨)"
                    self.send(f"당신의 chat_id: {chat}\n{tag}", chat_id=chat)
                    continue
                if chat != self.chat_id:  # 화이트리스트: 타인 전면 무시
                    continue
                try:
                    reply = self.handle(text)
                except Exception as e:
                    reply = f"조회 중 오류: {e}"
                self.send(reply, chat_id=chat)

    def _handle_callback(self, cq: dict) -> None:
        """인라인 버튼(승인/무시) 처리."""
        self._call("answerCallbackQuery", callback_query_id=cq.get("id"))
        chat = str(cq.get("from", {}).get("id", ""))
        if chat != self.chat_id:
            return
        data = cq.get("data", "")
        if data == "skip":
            self.send("❌ 전체 무시. 오늘은 현금 대기합니다.")
            return
        if data == "hold":
            self.send("✅ 전체 보유 유지. 매도 안 함.")
            return
        if data == "usignore":
            self.send("❌ 미국 후보 무시. 오늘 밤은 페이퍼 매매 안 함.")
            return
        if data == "dignore":
            self.send("❌ 역추세 후보 무시.")
            return
        if data.startswith("dbuy:"):  # 트랙 D 역추세 매수 예약
            sym = data.split(":", 1)[1]
            from ..scanner import _us_meta
            paper_track_d.add_reservation("buy", sym, _us_meta(sym)[0])
            self.send(f"📌 역추세 매수 예약: {sym} — 새벽 개장 후 기록.")
            return
        if data.startswith("dsell:"):  # 트랙 D 역추세 매도 예약
            sym = data.split(":", 1)[1]
            name = paper_track_d.load_ledger()["positions"].get(sym, {}).get("name", sym)
            paper_track_d.add_reservation("sell", sym, name)
            self.send(f"📌 역추세 매도 예약: {name}({sym}) — 새벽 개장 후 기록.")
            return
        if data.startswith("usbuy:"):  # 미국 페이퍼 매수 예약
            sym = data.split(":", 1)[1]
            from ..scanner import _us_meta
            name = _us_meta(sym)[0]
            paper_us.add_reservation("buy", sym, name)
            self.send(f"📌 매수 예약: {name}({sym}) — 새벽 개장 후 실시간가로 장부 기록돼요.")
            return
        if data.startswith("ussell:"):  # 미국 페이퍼 매도 예약
            sym = data.split(":", 1)[1]
            led = paper_us.load_ledger()
            name = led["positions"].get(sym, {}).get("name", sym)
            paper_us.add_reservation("sell", sym, name)
            self.send(f"📌 매도 예약: {name}({sym}) — 새벽 개장 후 기록돼요.")
            return
        if data == "longignore":
            self.send("📗 보류. 이번엔 편입 안 함.")
            return
        if data == "longhold":
            self.send("📗 전체 보유 유지 (장기).")
            return
        if data.startswith("longbuy:"):  # 트랙 C 장기 편입 예약 (개장 후 기록)
            sym = data.split(":", 1)[1]
            from ..scanner import _us_meta
            name = _us_meta(sym)[0]
            paper_long.add_reservation("buy", sym, name)
            self.send(f"📌 장기 편입 예약: {name}({sym}) — 새벽 개장 후 실시간가로 기록돼요.")
            return
        if data.startswith("longsell:"):  # 트랙 C 장기 매도 예약 (개장 후 기록)
            sym = data.split(":", 1)[1]
            led = paper_long.load_ledger()
            name = led["positions"].get(sym, {}).get("name", sym)
            paper_long.add_reservation("sell", sym, name)
            self.send(f"📌 장기 매도 예약: {name}({sym}) — 새벽 개장 후 기록돼요.")
            return
        if data.startswith("buy:"):
            code = data.split(":", 1)[1]
            if code not in self._pending_codes():
                self.send("만료됐거나 오늘 후보가 아닌 종목이에요.")
                return
            self.send(f"주문 처리 중... ({code})")
            self.send(self.execute_buy(code))
        elif data.startswith("sell:"):  # 주간 재평가 승인 매도
            code = data.split(":", 1)[1]
            self.send(f"매도 처리 중... ({code})")
            self.send(self.execute_sell(code))
