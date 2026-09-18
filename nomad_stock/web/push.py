"""웹 푸시 알림 (PWA). 텔레그램 대체 — 폰으로 알림을 민다.

- VAPID 키: 최초 1회 자동 생성 후 state/vapid_private.pem에 보관(볼륨 유지).
- 구독: 브라우저가 보낸 구독정보를 state/push_subs.json에 저장.
- 발송: 저장된 모든 구독에 푸시. 죽은 구독(404/410)은 자동 정리.
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import serialization

from ..paper_us import _STATE_DIR

_VAPID_PEM = os.path.join(_STATE_DIR, "vapid_private.pem")
_SUBS = os.path.join(_STATE_DIR, "push_subs.json")
_CLAIMS_SUB = "mailto:nomadnu@gmail.com"   # VAPID 연락처(푸시 서비스용, 공개 안 됨)


def _vapid():
    from py_vapid import Vapid01
    if not os.path.exists(_VAPID_PEM):
        v = Vapid01()
        v.generate_keys()
        v.save_key(_VAPID_PEM)
    return Vapid01.from_file(_VAPID_PEM)


def public_key() -> str:
    """프론트가 구독에 쓰는 applicationServerKey (base64url, uncompressed point)."""
    raw = _vapid().public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _load_subs() -> list:
    if os.path.exists(_SUBS):
        try:
            with open(_SUBS, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_subs(subs: list) -> None:
    with open(_SUBS, "w", encoding="utf-8") as f:
        json.dump(subs, f)


def add_sub(sub: dict) -> None:
    subs = [s for s in _load_subs() if s.get("endpoint") != sub.get("endpoint")]
    subs.append(sub)
    _save_subs(subs)


def sub_count() -> int:
    return len(_load_subs())


def send_push(title: str, body: str, url: str = "/") -> int:
    """저장된 모든 구독에 푸시. 보낸 개수 반환. 죽은 구독은 정리."""
    subs = _load_subs()
    if not subs:
        return 0
    from pywebpush import WebPushException, webpush
    data = json.dumps({"title": title, "body": body, "url": url})
    pem = open(_VAPID_PEM, encoding="utf-8").read()
    alive, sent = [], 0
    for s in subs:
        try:
            webpush(subscription_info=s, data=data, vapid_private_key=pem,
                    vapid_claims={"sub": _CLAIMS_SUB})
            alive.append(s)
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                continue  # 만료된 구독 → 제거
            alive.append(s)
        except Exception:
            alive.append(s)
    if len(alive) != len(subs):
        _save_subs(alive)
    return sent
