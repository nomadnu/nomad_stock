"""체결/이벤트 알림 — 텔레그램 + 이메일 + 콘솔.

설정되지 않은 채널은 자동으로 비활성. 알림 전송 실패가 매매를 막지 않도록
모든 전송 오류는 삼켜서 경고만 남긴다(자동매매 도중 죽지 않게).
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

import requests


class Notifier:
    """여러 채널로 동시에 알림을 보낸다."""

    def __init__(self, channels: list):
        self.channels = channels

    def send(self, text: str) -> None:
        for ch in self.channels:
            try:
                ch(text)
            except Exception as e:  # 알림 실패가 매매를 멈추면 안 됨
                print(f"[알림 실패: {getattr(ch, '__name__', ch)}] {e!r}")

    @property
    def active_channels(self) -> list[str]:
        return [getattr(ch, "label", ch.__name__) for ch in self.channels]


# ----- 채널 구현 (각자 호출 가능한 함수) ---------------------------------
def _console_channel(text: str) -> None:
    print(f"[알림] {text}")


_console_channel.label = "console"


def _make_telegram(token: str, chat_id: str):
    def telegram(text: str) -> None:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url, json={"chat_id": chat_id, "text": text}, timeout=10
        )
        resp.raise_for_status()

    telegram.label = "telegram"
    return telegram


def _make_email(host: str, port: int, user: str, password: str, to_addr: str):
    def email(text: str) -> None:
        msg = MIMEText(text, _charset="utf-8")
        msg["Subject"] = "[nomad_stock] 매매 알림"
        msg["From"] = user
        msg["To"] = to_addr
        with smtplib.SMTP_SSL(host, port, timeout=10) as server:
            server.login(user, password)
            server.send_message(msg)

    email.label = "email"
    return email


def build_notifier() -> Notifier:
    """.env/환경변수에서 설정된 채널만 모아 Notifier를 만든다.

    콘솔은 항상 포함. 텔레그램/이메일은 값이 채워진 경우에만 활성화.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    channels = [_console_channel]

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        channels.append(_make_telegram(tg_token, tg_chat))

    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    smtp_to = os.getenv("NOTIFY_EMAIL_TO", "").strip()
    if smtp_user and smtp_pass and smtp_to:
        channels.append(
            _make_email(
                host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
                port=int(os.getenv("SMTP_PORT", "465")),
                user=smtp_user,
                password=smtp_pass,
                to_addr=smtp_to,
            )
        )

    return Notifier(channels)
