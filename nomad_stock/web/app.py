"""웹 대시보드 백엔드 (Flask).

API:
  GET /                → 대시보드 HTML
  GET /api/summary     → 예수금/총평가/평가손익 + 보유종목
  GET /api/trades      → 최근 매매 로그
  GET /api/watchlist   → watchlist 종목별 현재 전략 신호

KIS 모의/실거래 조회를 그대로 쓰므로 .env 설정이 필요하다.
기본은 localhost(127.0.0.1)에만 바인딩 — 계좌정보 노출 방지.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from functools import wraps

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

from ..broker import KISClient
from ..data.loader import load_ohlcv
from ..live.market_hours import market_status
from ..strategy import make_strategy

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def create_app() -> Flask:
    app = Flask(__name__)
    client = KISClient()

    # 로그인 설정. 비밀번호가 있으면 인증을 켠다(외부 공개 대비).
    password = os.getenv("DASHBOARD_PASSWORD", "").strip()
    app.secret_key = os.getenv("DASHBOARD_SECRET", "").strip() or secrets.token_hex(16)
    auth_on = bool(password)

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if auth_on and not session.get("ok"):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "unauthorized"}), 401
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not auth_on:
            return redirect(url_for("index"))
        error = ""
        if request.method == "POST":
            pw = request.form.get("password", "")
            if hmac.compare_digest(pw, password):
                session["ok"] = True
                session.permanent = True
                return redirect(url_for("index"))
            error = "비밀번호가 올바르지 않습니다."
        return render_template_string(_LOGIN_HTML, error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        return render_template_string(_HTML, env=client.cfg.env, auth=auth_on)

    @app.route("/api/summary")
    @login_required
    def api_summary():
        bal = client.get_balance()
        for h in bal["holdings"]:
            h["pnl_pct"] = (h["cur_price"] / h["avg_price"] - 1.0) * 100 if h["avg_price"] else 0.0
            h["value"] = h["qty"] * h["cur_price"]
        total_pnl = sum(h["eval_pnl"] for h in bal["holdings"])
        return jsonify(
            {
                "env": client.cfg.env,
                "account": f"{client.cfg.cano}-{client.cfg.acnt_prdt_cd}",
                "market": market_status(),
                "cash": bal["cash"],
                "total_eval": bal["total_eval"],
                "total_pnl": total_pnl,
                "holdings": bal["holdings"],
            }
        )

    @app.route("/api/trades")
    @login_required
    def api_trades():
        path = os.path.join(_ROOT, "logs", "trades.log")
        lines = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                lines = [l.rstrip() for l in f.readlines()[-40:]]
        return jsonify({"lines": lines})

    @app.route("/api/watchlist")
    @login_required
    def api_watchlist():
        path = os.path.join(_ROOT, "watchlist.json")
        out = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            for it in cfg.get("items", []):
                try:
                    df = load_ohlcv(it["symbol"], start="2023-01-01", use_cache=False)
                    strat = make_strategy(it.get("strategy", "sma"), **it.get("params", {}))
                    sig = float(strat.generate_signals(df).iloc[-1])
                    out.append(
                        {
                            "symbol": it["symbol"],
                            "strategy": strat.name,
                            "signal": "매수보유" if sig >= 1 else "현금",
                        }
                    )
                except Exception as e:
                    out.append({"symbol": it["symbol"], "strategy": "?", "signal": f"오류: {e}"})
        return jsonify({"items": out})

    @app.route("/manifest.json")
    def manifest():
        # PWA 매니페스트 (안드로이드 홈화면 아이콘). 로그인 불필요.
        return jsonify(
            {
                "name": "nomad_stock",
                "short_name": "nomad_stock",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#0f1420",
                "theme_color": "#0f1420",
                "icons": [
                    {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                ],
            }
        )

    return app


_LOGIN_HTML = """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>nomad_stock 로그인</title>
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="icon" type="image/png" href="/static/icon-192.png">
<link rel="manifest" href="/manifest.json">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="nomad_stock">
<meta name="theme-color" content="#0f1420">
<style>
  body{margin:0;background:#0f1420;color:#e6edf6;font-family:'Malgun Gothic',system-ui,sans-serif;
       display:flex;min-height:100vh;align-items:center;justify-content:center}
  .box{background:#1a2235;border:1px solid #2a3550;border-radius:14px;padding:28px;width:300px}
  h1{font-size:18px;margin:0 0 4px} .sub{color:#8a98b4;font-size:13px;margin-bottom:18px}
  input{width:100%;box-sizing:border-box;padding:11px;border-radius:8px;border:1px solid #2a3550;
        background:#0f1420;color:#e6edf6;font-size:15px;margin-bottom:12px}
  button{width:100%;padding:11px;border:0;border-radius:8px;background:#4fd1a5;color:#0f1420;
         font-size:15px;font-weight:700;cursor:pointer}
  .err{color:#ff5a5a;font-size:13px;margin-bottom:10px}
</style></head>
<body><form class="box" method="post">
  <h1>📈 nomad_stock</h1><div class="sub">대시보드 로그인</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <input type="password" name="password" placeholder="비밀번호" autofocus>
  <button type="submit">로그인</button>
</form></body></html>"""


_HTML = """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>nomad_stock 대시보드</title>
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="icon" type="image/png" href="/static/icon-192.png">
<link rel="manifest" href="/manifest.json">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="nomad_stock">
<meta name="theme-color" content="#0f1420">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root{--bg:#0f1420;--card:#1a2235;--line:#2a3550;--txt:#e6edf6;--sub:#8a98b4;--up:#ff5a5a;--down:#3d8bff;--accent:#4fd1a5}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font-family:'Malgun Gothic',system-ui,sans-serif}
  .wrap{max-width:960px;margin:0 auto;padding:16px}
  h1{font-size:20px;margin:8px 0} .muted{color:var(--sub);font-size:13px}
  .cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
  .card .label{color:var(--sub);font-size:12px} .card .val{font-size:22px;font-weight:700;margin-top:6px}
  table{width:100%;border-collapse:collapse;margin-top:8px;font-size:14px}
  th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--sub);font-weight:500;font-size:12px}
  .up{color:var(--up)} .down{color:var(--down)}
  .sec{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}
  .sec h2{font-size:15px;margin:0 0 8px} .row{display:flex;gap:14px;flex-wrap:wrap}
  .row .sec{flex:1;min-width:280px}
  pre{white-space:pre-wrap;font-size:12px;color:var(--sub);max-height:240px;overflow:auto;margin:0}
  .pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px}
  .pill.buy{background:rgba(79,209,165,.15);color:var(--accent)} .pill.cash{background:rgba(138,152,180,.15);color:var(--sub)}
  button{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
  @media(max-width:640px){.cards{grid-template-columns:1fr}}
</style></head>
<body><div class="wrap">
  <h1>📈 nomad_stock 대시보드 <span class="muted" id="badge"></span></h1>
  <div class="muted" id="meta">불러오는 중…</div>
  <div class="cards">
    <div class="card"><div class="label">예수금</div><div class="val" id="cash">-</div></div>
    <div class="card"><div class="label">총평가금액</div><div class="val" id="total">-</div></div>
    <div class="card"><div class="label">평가손익</div><div class="val" id="pnl">-</div></div>
  </div>
  <div class="row">
    <div class="sec" style="flex:2"><h2>보유 종목</h2><table id="holdings">
      <thead><tr><th>종목</th><th>수량</th><th>평균가</th><th>현재가</th><th>평가금액</th><th>손익률</th></tr></thead>
      <tbody></tbody></table><div class="muted" id="noh" style="display:none">보유 종목 없음</div></div>
    <div class="sec" style="flex:1"><h2>비중</h2><canvas id="pie" height="220"></canvas></div>
  </div>
  <div class="row">
    <div class="sec"><h2>전략 신호 (watchlist)</h2><table id="wl">
      <thead><tr><th>종목</th><th>전략</th><th>신호</th></tr></thead><tbody></tbody></table></div>
    <div class="sec"><h2>최근 매매 로그</h2><pre id="log">-</pre></div>
  </div>
  <div style="margin:14px 0"><button onclick="loadAll()">새로고침</button>
    {% if auth %}<a href="/logout" style="color:#8a98b4;font-size:13px;margin-left:10px">로그아웃</a>{% endif %}
    <span class="muted" id="updated"></span></div>
</div>
<script>
const won = n => (n==null?'-':n.toLocaleString('ko-KR')+'원');
const cls = v => v>0?'up':(v<0?'down':'');
const sign = v => (v>0?'+':'')+v.toFixed(2)+'%';
let pie;
async function loadAll(){
  try{
    const s = await (await fetch('/api/summary')).json();
    document.getElementById('badge').textContent = '('+(s.env==='real'?'실거래':'모의투자')+' · '+s.account+')';
    document.getElementById('meta').textContent = s.market;
    document.getElementById('cash').textContent = won(s.cash);
    document.getElementById('total').textContent = won(s.total_eval);
    const p = document.getElementById('pnl'); p.textContent = won(s.total_pnl); p.className='val '+cls(s.total_pnl);
    const tb = document.querySelector('#holdings tbody'); tb.innerHTML='';
    document.getElementById('noh').style.display = s.holdings.length?'none':'block';
    s.holdings.forEach(h=>{
      tb.innerHTML += `<tr><td>${h.name}<br><span class="muted">${h.symbol}</span></td>
        <td>${h.qty}</td><td>${won(h.avg_price)}</td><td>${won(h.cur_price)}</td>
        <td>${won(h.value)}</td><td class="${cls(h.pnl_pct)}">${sign(h.pnl_pct)}</td></tr>`;
    });
    drawPie(s.holdings, s.cash);
    const w = await (await fetch('/api/watchlist')).json();
    const wb = document.querySelector('#wl tbody'); wb.innerHTML='';
    w.items.forEach(i=>{ const buy=i.signal==='매수보유';
      wb.innerHTML += `<tr><td>${i.symbol}</td><td>${i.strategy}</td>
        <td><span class="pill ${buy?'buy':'cash'}">${i.signal}</span></td></tr>`; });
    const t = await (await fetch('/api/trades')).json();
    document.getElementById('log').textContent = t.lines.join('\\n') || '아직 매매 기록 없음';
    document.getElementById('updated').textContent = '갱신: '+new Date().toLocaleTimeString('ko-KR');
  }catch(e){ document.getElementById('meta').textContent='조회 실패: '+e; }
}
function drawPie(holdings, cash){
  const labels = holdings.map(h=>h.name).concat(['현금']);
  const data = holdings.map(h=>h.value).concat([cash]);
  const colors = ['#4fd1a5','#3d8bff','#ff5a5a','#f7b731','#a55eea','#26de81','#778ca3'];
  if(pie) pie.destroy();
  pie = new Chart(document.getElementById('pie'),{type:'doughnut',
    data:{labels,datasets:[{data,backgroundColor:colors,borderColor:'#1a2235',borderWidth:2}]},
    options:{plugins:{legend:{labels:{color:'#8a98b4',font:{size:11}}}}}});
}
loadAll(); setInterval(loadAll, 30000);
</script>
</body></html>"""
