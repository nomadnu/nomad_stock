# 24시간 클라우드 운영 가이드

PC를 꺼도 자동매매가 돌도록 소형 서버(클라우드 VM, 라즈베리파이 등)에 올린다.
시간대(KST)와 자격증명(`.env`)만 맞추면 된다.

> **반드시 `KIS_ENV=paper`(모의투자)로 충분히 검증한 뒤** 실거래로 전환할 것.

## 사전 준비

- 소형 리눅스 서버 (예: AWS Lightsail/EC2 t4g.nano, Oracle Cloud Free Tier, 라즈베리파이)
- 서버에 코드 복사 후 `.env` 작성 (로컬 `.env`를 그대로 복사해도 됨)
- `watchlist.json`에 운용할 종목/전략/예산 설정

## 방법 A · Docker (권장)

```bash
# 서버에서
git clone <레포> nomad_stock && cd nomad_stock
cp .env.example .env && nano .env        # KIS 키 입력

docker compose up -d --build             # 백그라운드 상주 시작
docker compose logs -f                   # 로그 실시간 확인
docker compose down                      # 중지
```

- `restart: unless-stopped` 라 컨테이너가 죽거나 서버가 재부팅돼도 자동 재시작.
- 시간대는 이미지에서 `Asia/Seoul`로 고정 → 장시간 판단 정확.
- `logs/`, `.tokens/`, `watchlist.json`은 호스트와 공유되어 재빌드 없이 수정 가능.

## 방법 B · systemd (Docker 없이)

```bash
sudo useradd -r -m -d /opt/nomad_stock trader
sudo cp -r . /opt/nomad_stock && cd /opt/nomad_stock
sudo -u trader python3 -m pip install -r requirements.txt

sudo cp deploy/nomad_stock.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nomad_stock
journalctl -u nomad_stock -f             # 로그
```

> 서버 시간대가 UTC면 KST로: `sudo timedatectl set-timezone Asia/Seoul`

## 웹 대시보드 외부 공개 (회사·폰에서 보기)

`docker compose up -d`를 하면 `trader`(자동매매)와 `dashboard`(웹) 두 컨테이너가
함께 뜬다. 대시보드는 `5000` 포트로 열린다.

### 1. 로그인·세션키 설정 (외부 공개 시 필수)
`.env`에 추가:
```bash
DASHBOARD_PASSWORD=원하는_비밀번호
DASHBOARD_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
```
비밀번호가 없으면 누구나 계좌를 보게 되므로, 외부 공개 전 반드시 설정한다.

### 2. 배포 (클라우드 VM 예시)
```bash
# 1) 소형 VM 준비: Oracle Cloud 무료티어 / AWS Lightsail($3~) / 라즈베리파이 등
# 2) 도커 설치 후, 코드 복사 + .env 작성
docker compose up -d --build
# 3) 방화벽/보안그룹에서 5000 포트 열기 (가능하면 '내 회사 IP'만 허용)
```
접속: `http://<서버IP>:5000` → 로그인 → 대시보드.

### 3. HTTPS (강력 권장)
`http`로 열면 로그인 비밀번호가 암호화 없이 오간다. 둘 중 하나로 https를 붙인다.

- **도메인이 있으면** — Caddy 리버스 프록시로 자동 HTTPS:
  ```
  # Caddyfile
  your.domain.com {
      reverse_proxy dashboard:5000
  }
  ```
- **도메인이 없으면** — Cloudflare Tunnel(무료)로 https 주소 발급:
  ```bash
  cloudflared tunnel --url http://localhost:5000
  ```
  → `https://랜덤이름.trycloudflare.com` 주소가 생기고, 이 주소로 어디서나 접속.

> 보안 최소수칙: ① 강한 비밀번호 ② https ③ 가능하면 접속 IP 제한.
> 모의투자 단계에선 위험이 낮지만, 실거래 전환 전 반드시 갖출 것.

## 운영 모드 선택

| 실행 대상 | 설명 | command |
|-----------|------|---------|
| 일봉 스케줄러 | 매일 `run_time`에 watchlist 매매 (기본) | `python run_scheduler.py --live` |
| 포트폴리오 | 총자본 비중 리밸런싱 | `python run_portfolio.py --live` |
| 변동성 돌파 | 장중 돌파 감시 | `python run_breakout_live.py 005930 --live` |

Docker는 `docker-compose.yml`의 `command`, systemd는 유닛의 `ExecStart`를 바꾼다.

## 체크리스트

- [ ] `.env`의 `KIS_ENV` 확인 (처음엔 `paper`)
- [ ] 서버 시간대 = `Asia/Seoul`
- [ ] 텔레그램/이메일 알림 설정 (무인 운영 시 사실상 필수)
- [ ] 며칠간 로그/알림으로 정상 동작 확인 후에만 `real` 전환
- [ ] 토큰은 24h마다 자동 재발급 (`.tokens/` 영속화로 호출 절약)
