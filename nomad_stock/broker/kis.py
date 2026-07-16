"""한국투자증권(KIS) Open API REST 클라이언트.

모의투자(paper)와 실거래(real)를 KIS_ENV 환경변수로 전환한다.
지원 기능:
  - 접근토큰 발급/캐싱 (24시간 유효, 파일에 저장해 재사용)
  - 현재가 조회
  - 현금 매수/매도 주문
  - 계좌 잔고 조회

주의: TR_ID 등 일부 스펙은 KIS 문서 기준이며, 정책 변경 시
      https://apiportal.koreainvestment.com 의 최신 문서로 확인할 것.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

# 모의투자 / 실거래 도메인
_PAPER_BASE = "https://openapivts.koreainvestment.com:29443"
_REAL_BASE = "https://openapi.koreainvestment.com:9443"

# 주문 TR_ID (현금주문 order-cash). V=모의(virtual), T=실거래.
_TR = {
    "paper": {"buy": "VTTC0802U", "sell": "VTTC0801U", "balance": "VTTC8434R"},
    "real": {"buy": "TTTC0802U", "sell": "TTTC0801U", "balance": "TTTC8434R"},
}
# 현재가 조회 TR_ID는 모의/실거래 동일
_TR_PRICE = "FHKST01010100"

# 토큰 캐시 파일
_TOKEN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".tokens"
)


@dataclass
class KISConfig:
    app_key: str
    app_secret: str
    account_no: str          # "12345678-01" 형식
    env: str = "paper"       # "paper" | "real"

    @property
    def base_url(self) -> str:
        return _PAPER_BASE if self.env == "paper" else _REAL_BASE

    @property
    def cano(self) -> str:
        """계좌 앞 8자리(종합계좌번호)."""
        return self._digits[:8]

    @property
    def acnt_prdt_cd(self) -> str:
        """계좌 뒤 2자리(상품코드). 없으면 위탁계좌 기본값 '01'."""
        rest = self._digits[8:]
        return rest if len(rest) == 2 else "01"

    @property
    def _digits(self) -> str:
        """계좌번호에서 숫자만 추출 (하이픈/공백 무시)."""
        return "".join(c for c in self.account_no if c.isdigit())

    @classmethod
    def from_env(cls) -> "KISConfig":
        """`.env` 또는 환경변수에서 설정을 읽는다."""
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

        key = os.getenv("KIS_APP_KEY")
        secret = os.getenv("KIS_APP_SECRET")
        account = os.getenv("KIS_ACCOUNT_NO")
        env = os.getenv("KIS_ENV", "paper").strip().lower()

        missing = [
            n
            for n, v in [
                ("KIS_APP_KEY", key),
                ("KIS_APP_SECRET", secret),
                ("KIS_ACCOUNT_NO", account),
            ]
            if not v
        ]
        if missing:
            raise RuntimeError(
                f".env에 다음 값이 필요합니다: {', '.join(missing)} "
                f"(.env.example 참고)"
            )
        if env not in ("paper", "real"):
            raise ValueError("KIS_ENV는 'paper' 또는 'real' 이어야 합니다.")
        return cls(app_key=key, app_secret=secret, account_no=account, env=env)


class KISClient:
    def __init__(self, config: KISConfig | None = None):
        self.cfg = config or KISConfig.from_env()
        self._token: str | None = None
        self._token_expire: datetime | None = None

    # ----- 인증 ---------------------------------------------------------
    def _token_cache_path(self) -> str:
        os.makedirs(_TOKEN_DIR, exist_ok=True)
        return os.path.join(_TOKEN_DIR, f"token_{self.cfg.env}.json")

    def _load_cached_token(self) -> bool:
        path = self._token_cache_path()
        if not os.path.exists(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            expire = datetime.fromisoformat(data["expire"])
            # 만료 10분 전이면 갱신
            if expire - timedelta(minutes=10) > datetime.now():
                self._token = data["token"]
                self._token_expire = expire
                return True
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
        return False

    def _save_token(self, token: str, expire: datetime) -> None:
        with open(self._token_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"token": token, "expire": expire.isoformat()}, f)

    def token(self) -> str:
        """유효한 접근토큰을 반환(필요 시 발급). KIS는 토큰 재발급을 분당 1회로 제한."""
        if self._token and self._token_expire and self._token_expire > datetime.now():
            return self._token
        if self._load_cached_token():
            return self._token  # type: ignore[return-value]

        url = f"{self.cfg.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
        }
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"토큰 발급 실패: {data}")
        self._token = data["access_token"]
        # expires_in(초). 보수적으로 expires_in 사용, 없으면 24h.
        secs = int(data.get("expires_in", 86400))
        self._token_expire = datetime.now() + timedelta(seconds=secs)
        self._save_token(self._token, self._token_expire)
        return self._token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }

    def _request(
        self, method: str, url: str, *, retries: int = 3, **kwargs
    ) -> requests.Response:
        """HTTP 요청 + 일시적 5xx/연결오류 자동 재시도.

        KIS 모의투자 서버는 간헐적으로 500을 반환한다. 5xx면 잠깐 쉬고 재시도.
        4xx(요청 오류)는 재시도 의미가 없으므로 즉시 raise.
        """
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = requests.request(method, url, timeout=10, **kwargs)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return resp
                last_exc = requests.HTTPError(f"{resp.status_code} {resp.reason}")
            except requests.ConnectionError as e:
                last_exc = e
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))  # 0.5s, 1.0s 백오프
        raise RuntimeError(f"KIS 요청 실패(재시도 {retries}회): {last_exc}")

    # ----- 시세 ---------------------------------------------------------
    def get_price(self, symbol: str) -> int:
        """현재가(원)를 정수로 반환. symbol은 6자리 종목코드."""
        url = f"{self.cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        resp = self._request("GET", url, headers=self._headers(_TR_PRICE), params=params)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"현재가 조회 실패: {data.get('msg1')}")
        return int(data["output"]["stck_prpr"])

    def get_quote(self, symbol: str) -> dict:
        """현재가·시가·고가·저가·전일종가를 함께 반환 (변동성 돌파용)."""
        url = f"{self.cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        resp = self._request("GET", url, headers=self._headers(_TR_PRICE), params=params)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"시세 조회 실패: {data.get('msg1')}")
        o = data["output"]

        def _f(key, cast=int, default=0):
            try:
                return cast(o.get(key, default))
            except (ValueError, TypeError):
                return default

        return {
            "price": _f("stck_prpr"),        # 현재가
            "open": _f("stck_oprc"),         # 시가
            "high": _f("stck_hgpr"),         # 당일 고가
            "low": _f("stck_lwpr"),          # 당일 저가
            "prev_close": _f("stck_sdpr"),   # 전일 종가(기준가)
            "per": _f("per", float),         # PER (적자면 0 또는 음수)
            "pbr": _f("pbr", float),         # PBR
            "change_pct": _f("prdy_ctrt", float),  # 전일대비 등락률(%)
        }

    # ----- 재무 (트랙 D 한국 펀더멘털 3박자) -----------------------------
    def financial_ratio(self, symbol: str, annual: bool = True) -> dict | None:
        """국내주식 재무비율(최근 결산 기준). 실패하면 None.

        반환: {yymm, rev_growth, op_growth, ni_growth, roe, debt_ratio, eps, bps}
          증가율·ROE·부채비율은 % 단위, eps·bps는 원. KIS TR FHKST66430300.
        ⚠️ 모의 도메인이 재무 TR을 막으면 None이 올 수 있음 → 서버 실환경에서 검증 필요.
        """
        url = f"{self.cfg.base_url}/uapi/domestic-stock/v1/finance/financial-ratio"
        params = {
            "fid_div_cls_code": "0" if annual else "1",  # 0=년, 1=분기
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
        }
        try:
            resp = self._request("GET", url, headers=self._headers("FHKST66430300"), params=params)
            data = resp.json()
        except Exception:
            return None
        if data.get("rt_cd") != "0":
            return None
        rows = data.get("output") or []
        if not rows:
            return None
        o = rows[0]  # 최근 결산년월

        def _f(key):
            v = o.get(key, "")
            try:
                return float(v) if v not in ("", None) else None
            except (ValueError, TypeError):
                return None

        return {
            "yymm": o.get("stac_yymm", ""),
            "rev_growth": _f("grs"),            # 매출액 증가율 %
            "op_growth": _f("bsop_prfi_inrt"),  # 영업이익 증가율 %
            "ni_growth": _f("ntin_inrt"),       # 순이익 증가율 %
            "roe": _f("roe_val"),               # ROE %
            "debt_ratio": _f("lblt_rate"),      # 부채비율 %
            "eps": _f("eps"),
            "bps": _f("bps"),
        }

    # ----- 주문 ---------------------------------------------------------
    def order_cash(
        self,
        symbol: str,
        qty: int,
        side: str,
        price: int = 0,
        order_type: str = "01",  # 00=지정가, 01=시장가
    ) -> dict:
        """현금 매수/매도 주문.

        side: "buy" | "sell"
        price: 지정가일 때 주문가격(원). 시장가(order_type="01")면 0.
        반환: KIS 응답 dict (주문번호 등 output 포함).
        """
        if side not in ("buy", "sell"):
            raise ValueError("side는 'buy' 또는 'sell'")
        tr_id = _TR[self.cfg.env][side]
        url = f"{self.cfg.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.cfg.cano,
            "ACNT_PRDT_CD": self.cfg.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        resp = self._request("POST", url, headers=self._headers(tr_id), json=body)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"주문 실패: {data.get('msg1')} ({data.get('msg_cd')})")
        return data

    # ----- 잔고 ---------------------------------------------------------
    def get_balance(self) -> dict:
        """계좌 잔고 조회. 보유종목 리스트와 예수금 등 요약을 반환."""
        tr_id = _TR[self.cfg.env]["balance"]
        url = f"{self.cfg.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cfg.cano,
            "ACNT_PRDT_CD": self.cfg.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        resp = self._request("GET", url, headers=self._headers(tr_id), params=params)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"잔고 조회 실패: {data.get('msg1')}")
        holdings = [
            {
                "symbol": row["pdno"],
                "name": row["prdt_name"],
                "qty": int(row["hldg_qty"]),
                "avg_price": float(row["pchs_avg_pric"]),
                "cur_price": int(row["prpr"]),
                "eval_pnl": int(row["evlu_pfls_amt"]),
            }
            for row in data.get("output1", [])
            if int(row.get("hldg_qty", 0)) > 0
        ]
        summary = data.get("output2", [{}])[0]
        return {
            "holdings": holdings,
            "cash": int(summary.get("dnca_tot_amt", 0)),       # 예수금총액
            "total_eval": int(summary.get("tot_evlu_amt", 0)),  # 총평가금액
        }
