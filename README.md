# nomad_stock

한국투자증권(KIS) 연동을 목표로 하는 주식 자동매매 프로젝트.
**현재 단계: 백테스트 엔진** — 실거래 전에 전략을 과거 데이터로 검증한다.

## 빠른 시작

```bash
pip install -r requirements.txt

python run_backtest.py                 # 삼성전자(005930), SMA 20x60
python run_backtest.py 000660 10 30    # SK하이닉스, SMA 10x30
python run_backtest.py AAPL 20 60 2020-01-01   # 애플
```

종목코드: 국내는 6자리(예: `005930`), 해외는 티커(예: `AAPL`).

## 구조

```
nomad_stock/
├── data/loader.py          # FinanceDataReader 일봉 로더 (+ CSV 캐시)
├── strategy/
│   ├── base.py             # Strategy 인터페이스 (generate_signals)
│   ├── sma_cross.py        # 이동평균 교차 (추세추종)
│   ├── rsi.py              # RSI 평균회귀
│   ├── breakout.py         # 변동성 돌파 + 전용 백테스트
│   └── __init__.py         # make_strategy 팩토리
└── backtest/
    ├── engine.py           # 벡터화 백테스트 (1봉 지연 체결, 수수료/세금 반영)
    └── metrics.py          # CAGR / MDD / Sharpe / 거래횟수
run_backtest.py             # 실행 예제 (전략 vs Buy&Hold 비교)
```

## 설계 원칙

- **미래 참조 금지**: t일 신호는 t+1일에 체결된다 (`engine.py`의 `position = target.shift(1)`).
- **거래비용 반영**: 편도 수수료 0.015% + 국내 매도세 0.20% + 선택적 슬리피지.
- **전략 = 신호 생성기**: `Strategy.generate_signals()`만 구현하면 백테스트와
  (향후) 실거래 실행기에 동일하게 꽂힌다. 목표 포지션 0~1 비중.

## 수익곡선 그래프

`--plot`을 붙이면 자산곡선 + 낙폭(drawdown)을 `charts/`에 PNG로 저장한다.

```bash
python compare_strategies.py 005930 --plot     # 전략별 자산곡선 비교
python run_walkforward.py 005930 sma --plot    # 워크포워드 OOS vs 단순보유
python run_riskbacktest.py 005930 20 60 0.08 0.25 --plot   # 손절/익절 효과
```

## 백테스트 단계 손절/익절

장중 고가/저가로 손절·익절 체결을 시뮬레이션하는 이벤트 루프 엔진
(`backtest/risk_engine.py`). 종가-종가 기본 엔진으로는 못 하는 장중 청산을 반영한다.

```bash
python run_riskbacktest.py 005930 20 60 0.08 0.25   # 리스크없음 vs 손절8%/익절25%
```

> **교훈:** 추세추종(SMA)에 익절을 걸면 상승장 초입에 빠져나와 큰 추세를
> 놓친다(차트로 확인됨). 손절/익절은 평균회귀 전략이나 변동성 큰 종목에서
> 효과가 다르므로, 적용 전 이 백테스트로 꼭 검증할 것.

## 전략

| 전략 | 클래스/함수 | 종류 | 백테스트 | 실거래 |
|------|------------|------|:--------:|:------:|
| 이동평균 교차 | `SmaCrossStrategy` (`sma`) | 추세추종 | ✅ | ✅ |
| RSI 평균회귀 | `RsiStrategy` (`rsi`) | 역추세 | ✅ | ✅ |
| 볼린저밴드 | `BollingerStrategy` (`bb`) | 평균회귀 | ✅ | ✅ |
| 변동성 돌파 | `run_breakout_backtest` | 장중 돌파 | ✅ | ✅ (장중 상주 실행) |

여러 전략을 한 종목에 비교:
```bash
python compare_strategies.py            # 삼성전자: BuyHold/SMA/RSI/Breakout 비교표
python compare_strategies.py 000660 2020-01-01
```

> 변동성 돌파는 '당일 시가→종가' 구조라 종가기반 일봉 엔진과 맞지 않아
> 전용 백테스트(`strategy/breakout.py`)로 정확히 계산한다. 실거래는 장중
> 가격이 목표가를 돌파하는 순간을 잡아야 하므로 전용 상주 실행기를 쓴다(아래).

### 변동성 돌파 장중 자동매매

장중에 `목표가 = 당일시가 + k×전일변동폭` 돌파를 감시하다가, 돌파 시 시장가
매수하고 마감 직전(기본 15:15)에 전량 청산한다(오버나이트 미보유).

```bash
python run_breakout_live.py 005930            # 단일 종목, dry-run 감시
python run_breakout_live.py 005930,000660     # 여러 종목 동시
python run_breakout_live.py 005930 --live     # 실제 주문
```
환경변수: `BREAKOUT_K`(돌파계수 0.5), `BUDGET`(종목당 금액), `BREAKOUT_POLL`(폴링초 30),
`EXIT_TIME`(청산시각 15:15). 장 시간이 아니면 자동 대기한다.

## 웹 대시보드

브라우저에서 잔고·보유종목·손익·전략신호·매매로그를 한눈에 본다 (30초 자동 갱신).

```bash
python run_dashboard.py          # http://127.0.0.1:5000 (이 PC에서만)
python run_dashboard.py --lan    # 같은 와이파이의 폰에서도 접속
```

- 기본은 보안상 `localhost` 전용 (계좌정보 노출 방지).
- `--lan`이면 같은 네트워크의 폰 브라우저로 `http://<PC-IP>:5000` 접속 가능.
- **회사 등 외부에서** 보려면 클라우드 배포 필요([DEPLOY.md](DEPLOY.md)). 외부 접속 시
  반드시 인증/HTTPS를 앞단에 둘 것(현재는 인증 없음 → 외부 직접 노출 금지).

## 멀티 종목 포트폴리오 (자산배분 / 리밸런싱)

`portfolio.json`에 총자본과 종목별 목표 비중(weight)을 정하면, 전략 신호에
따라 목표 비중으로 리밸런싱한다. (목표금액 = 총자본 × 비중 × 신호)

```jsonc
{
  "total_capital": 10000000,
  "items": [
    { "symbol": "005930", "strategy": "sma", "params": {...}, "weight": 0.4 },
    { "symbol": "000660", "strategy": "rsi", "params": {...}, "weight": 0.3 },
    { "symbol": "035720", "strategy": "bb",  "params": {...}, "weight": 0.3 }
  ]
}
```

```bash
python run_portfolio.py            # 현황 + 리밸런싱 결정 (dry-run)
python run_portfolio.py --status   # 현재 보유 현황만
python run_portfolio.py --live     # 실제 리밸런싱 주문
```
신호가 0이 되면 자동 청산, 1이면 목표 비중만큼 매수. 비중 합이 1을 넘으면 거부.

## 리스크 관리 (손절 / 익절)

보유 종목의 손익률(현재가/평균매입가)이 손절선/익절선을 넘으면, **전략 신호와
무관하게 강제 청산**한다. 매 매매 사이클에서 전략 결정보다 **먼저** 점검한다.

`.env` 설정:
```bash
RISK_STOP_LOSS=0.05      # -5% 도달 시 손절 (0이면 끔)
RISK_TAKE_PROFIT=0.10    # +10% 도달 시 익절 (0이면 끔)
```

- `run_scheduler.py` / `run_portfolio.py`에 자동 연동 (설정돼 있으면 사이클마다 점검).
- 일봉 스케줄러는 하루 1회 점검이므로, **장중 촘촘한 손절**이 필요하면 단독 모니터:
  ```bash
  python run_risk.py            # 1회 점검 (dry-run)
  python run_risk.py --watch --live   # 장중 상주 반복 점검 + 실제 청산
  ```

> 기존 `.env`를 쓰고 있다면 위 두 줄을 추가해야 활성화된다(없으면 기능 꺼짐).

## 체결 알림 (텔레그램 / 이메일)

자동매매가 실제 체결되면 텔레그램·이메일로 통지한다. `.env`에 값을 채운 채널만
활성화되고(둘 다 비우면 콘솔/로그만), 알림 전송 실패는 매매를 멈추지 않는다.

```bash
# .env 설정 예
TELEGRAM_BOT_TOKEN=...   # @BotFather로 봇 생성
TELEGRAM_CHAT_ID=...     # 봇과 대화 시작 후 chat_id
# 또는 이메일(Gmail은 '앱 비밀번호' 사용)
SMTP_USER=you@gmail.com
SMTP_PASS=앱비밀번호
NOTIFY_EMAIL_TO=you@gmail.com
```
`run_scheduler.py`, `run_breakout_live.py` 실행 시 자동 연동된다.

## 파라미터 최적화 (격자탐색 + 과최적화 점검)

전략의 최적 파라미터를 자동 탐색한다. **과최적화를 막기 위해** 데이터를
인샘플(앞 70%, 학습)과 아웃샘플(뒤 30%, 검증)로 나눠서, 인샘플에서 찾은
파라미터가 '본 적 없는' 아웃샘플에서도 통하는지 확인한다.

```bash
python run_optimize.py 005930 sma      # 삼성전자 이동평균 최적화
python run_optimize.py 000660 rsi      # SK하이닉스 RSI 최적화
python run_optimize.py 005930 sma cagr # 정렬 기준 변경 (sharpe/cagr/total_return)
```

출력 끝의 진단이 핵심:
- `✓ 아웃샘플에서도 성과 유지` → 상대적으로 믿을 만함
- `⚠️ 과최적화 의심` / `⚠️ 음의 성과` → 그 파라미터로 실매매 금물

> 좋은 파라미터를 찾으면 `watchlist.json`의 `params`에 넣어 실거래에 반영한다.

### 워크포워드 최적화 (더 강한 과최적화 방어)

1회 분할보다 엄격하다. 시간을 굴려가며 **학습→다음 구간 적용→전진**을 반복하고,
각 검증구간(out-of-sample) 수익을 이어붙여 "매 시점 그때까지 데이터로만
최적화해 거래했을 때"의 현실적 수익곡선을 만든다.

```bash
python run_walkforward.py 005930 sma   # 폴드별 선택 파라미터 + 결합 OOS 성과
python run_walkforward.py 000660 rsi
```

출력은 ① 폴드별로 그때 뽑힌 파라미터와 성과(시기별로 어떻게 달라지는지),
② 전체 결합 OOS vs 단순보유 비교, ③ 실효성 진단을 보여준다. 결합 OOS가
단순보유보다 약하거나 음수면 그 전략/종목은 실전에 부적합하다는 신호다.

## 새 전략 만들기

`nomad_stock/strategy/`에 `Strategy`를 상속한 클래스를 추가:

```python
from nomad_stock.strategy.base import Strategy
import pandas as pd

class MyStrategy(Strategy):
    name = "MyStrategy"
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # df: Open/High/Low/Close/Volume
        # 반환: 목표 포지션 Series (1=보유, 0=현금)
        ...
```

## KIS 모의투자 연동 (페이퍼 트레이딩)

### 1. 자격증명 발급
1. [KIS Developers 포털](https://apiportal.koreainvestment.com) 로그인
2. 모바일/HTS에서 **모의투자 계좌** 개설 + 모의투자 신청
3. 포털에서 앱 등록 → **앱키(App Key) / 앱시크릿(App Secret)** 발급
4. `.env.example`를 `.env`로 복사하고 값 채우기:

```bash
cp .env.example .env   # Windows: copy .env.example .env
# .env 파일을 열어 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 입력
# KIS_ENV=paper (모의투자) 유지
```

### 2. 연결 테스트 (읽기 전용, 안전)
```bash
python test_kis.py           # 토큰 발급 + 현재가 + 잔고 조회
```

### 3. 자동매매 실행
```bash
python run_live.py                     # DRY-RUN: 주문 계산만, 실제 주문 X
python run_live.py 005930 20 60 --live # 실제 주문 (모의계좌)
```
`--live` 없이는 절대 주문이 나가지 않는다. 종목당 배분 금액은 `BUDGET` 환경변수로 조정.

> ⚠️ `KIS_ENV=real`로 바꾸면 **실제 돈**이 움직인다. 충분히 모의투자로 검증 후에만.

## 장중 자동 실행 스케줄러

`watchlist.json`에 종목·전략·예산을 정의하면, 매 거래일 지정 시각에 자동 매매한다.

```jsonc
{
  "run_time": "15:00",                 // 매일 이 시각에 실행
  "items": [
    { "symbol": "005930", "strategy": "sma", "params": { "fast": 20, "slow": 60 }, "budget": 1000000 },
    { "symbol": "000660", "strategy": "rsi", "params": { "period": 14, "oversold": 30, "exit_level": 50 }, "budget": 1000000 }
  ]
}
```
`strategy`: `"sma"`(이동평균 교차) 또는 `"rsi"`(평균회귀). `params`는 전략별 인자.

```bash
python run_scheduler.py            # 상주 모드, DRY-RUN (주문 안 함)
python run_scheduler.py --once     # 지금 즉시 1회 실행 후 종료
python run_scheduler.py --live     # 상주 모드, 실제 주문
```

- 결과는 콘솔과 `logs/trades.log`에 기록된다.
- 장 시간(평일 09:00~15:30)이 아니면 실제 주문 대신 결정만 출력한다.

### Windows 작업 스케줄러로 등록 (상주 프로세스 없이)

매 평일 14:50에 1회 실행하도록 등록 (PowerShell):

```powershell
$action  = New-ScheduledTaskAction -Execute "python" -Argument "run_scheduler.py --once --live" -WorkingDirectory "C:\nomad_stock"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 2:50PM
Register-ScheduledTask -TaskName "nomad_stock_daily" -Action $action -Trigger $trigger
```

## 구조 (연동 추가분)

```
nomad_stock/broker/kis.py            # KIS REST 클라이언트 (토큰/시세/주문/잔고, 5xx 재시도)
nomad_stock/live/runner.py           # 신호→주문 변환 러너 (dry-run 기본)
nomad_stock/live/breakout_runner.py  # 변동성 돌파 장중 실행기
nomad_stock/live/portfolio.py        # 멀티 종목 비중 리밸런싱
nomad_stock/live/risk.py             # 손절/익절 리스크 관리
nomad_stock/live/market_hours.py     # 장 운영시간 판단
nomad_stock/live/scheduler.py        # 거래일 지정 시각 실행
nomad_stock/notify/notifier.py       # 텔레그램/이메일/콘솔 알림
nomad_stock/web/app.py               # 웹 대시보드 (Flask)
nomad_stock/backtest/risk_engine.py  # 손절/익절 백테스트 (이벤트 루프)
nomad_stock/backtest/plot.py         # 자산곡선/낙폭 그래프
nomad_stock/optimize/grid.py         # 격자탐색 + 인/아웃샘플 점검
nomad_stock/optimize/walkforward.py  # 워크포워드 최적화
test_kis.py                          # 연결 테스트
run_live.py                          # 단일 종목 자동매매
run_scheduler.py                     # 관심종목 스케줄 자동매매 (+ 로깅/알림)
run_breakout_live.py                 # 변동성 돌파 장중 자동매매
run_portfolio.py                     # 멀티 종목 포트폴리오 리밸런싱
run_risk.py                          # 손절/익절 리스크 모니터
run_dashboard.py                     # 웹 대시보드 서버
run_optimize.py                      # 파라미터 최적화 (1회 분할)
run_walkforward.py                   # 워크포워드 최적화 (롤링)
run_riskbacktest.py                  # 손절/익절 백테스트 비교
compare_strategies.py                # 전략 비교
watchlist.json / portfolio.json      # 종목/전략/예산·비중 설정
Dockerfile / docker-compose.yml      # 24h 운영 (DEPLOY.md 참고)
```

## 로드맵

1. ✅ 백테스트 엔진 (단일 종목, 일봉)
2. ✅ **KIS API 연동** — 모의투자 페이퍼 트레이딩
3. ✅ 스케줄러(장중 자동 실행) + 로깅 + 관심종목(watchlist)
4. ✅ 전략 추가 (RSI 평균회귀, 변동성 돌파) + 전략 비교 도구
5. ✅ 파라미터 최적화(격자탐색) + 과최적화 점검(인/아웃샘플)
6. ✅ 체결 알림(텔레그램/이메일) + 볼린저밴드 + 변동성 돌파 장중 실행
7. ✅ 멀티 종목 포트폴리오(자산배분/리밸런싱) + 클라우드 24h 운영(Docker/systemd)
8. ✅ 손절/익절 리스크 관리 (사이클 점검 + 장중 모니터)
9. ✅ 워크포워드 최적화 (롤링 학습/검증)
10. ✅ 수익곡선 그래프 + 백테스트 단계 손절/익절 반영
11. ✅ 웹 대시보드 (잔고/손익/신호/로그 + 차트)
12. ⬜ 텔레그램 양방향 봇 + 대시보드 외부접속(인증) + 분봉

## 24시간 무인 운영

클라우드 서버/라즈베리파이에 올려 PC 없이 운영하는 방법은 **[DEPLOY.md](DEPLOY.md)** 참고
(Docker `docker compose up -d` 또는 systemd). 시간대는 KST로 고정된다.

> 앱키/시크릿은 `.env`로 분리하고 절대 커밋하지 않는다 (`.gitignore`에 포함됨).
```
