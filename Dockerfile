# 24시간 운영용 컨테이너. 시간대를 KST로 고정(장시간 판단의 기준).
FROM python:3.12-slim

ENV TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

# 시간대 데이터 설치 + KST 설정
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 기본: 스케줄러 상주 실행(실거래 주문). 모의/실거래는 .env의 KIS_ENV가 결정.
CMD ["python", "run_scheduler.py", "--live"]
