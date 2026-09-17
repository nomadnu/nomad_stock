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

    @app.route("/api/us")
    @login_required
    def api_us():
        from .. import paper_us
        try:
            return jsonify(paper_us.evaluate())
        except Exception as ex:  # KIS와 무관(FDR)이지만 안전하게 JSON으로
            return jsonify({"error": str(ex)}), 200

    @app.route("/api/long")
    @login_required
    def api_long():
        from .. import paper_long
        try:
            return jsonify(paper_long.evaluate())
        except Exception as ex:
            return jsonify({"error": str(ex)}), 200

    @app.route("/api/all")
    @login_required
    def api_all():
        # 펀더멘털 6트랙 (v1.4). 기존 트랙은 아카이브(대시보드 제외).
        from .. import paper_fund6
        from ..tracks import TRACKS
        tracks, holds = [], []
        for tid, meta in TRACKS.items():
            t = {"id": tid, "label": meta["label"], "flag": meta["flag"],
                 "color": meta["color"], "market": meta["market"], "strength": meta["strength"],
                 "bench_name": "코스피200" if meta["market"] == "KR" else "S&P500",
                 "bench_warn": meta["market"] == "KR"}
            try:
                e = paper_fund6.evaluate(tid)
                t["cap"] = e["capital_krw"]
                t["eval"] = e["total_krw"]
                t["ret"] = round(e["ret"], 2)
                t["holds"] = e["holds"]
                t["cash_pct"] = round(e["cash_pct"], 1)
                t["bench"] = round(e["bench_ret"], 2)
                t["edge"] = round(e["excess"], 2)
                t["halted"] = e["halted"]
                t["paused"] = e["paused"]
                for r in e["rows"]:
                    holds.append({"name": r["name"], "track": tid, "flag": meta["flag"],
                                  "color": meta["color"], "buy": r["buy_disp"],
                                  "cur": r["cur_disp"], "pct": round(r["pct"], 1)})
            except Exception as ex:
                t["cap"] = 10_000_000
                t["error"] = str(ex)[:60]
            tracks.append(t)

        total_cap = sum(t.get("cap", 10_000_000) for t in tracks)
        total_eval = sum(t.get("eval", t.get("cap", 10_000_000)) for t in tracks)
        total_ret = round((total_eval / total_cap - 1) * 100, 2) if total_cap else 0
        # 총 시장평균: 신뢰 가능한 미국(S&P500)만
        us_bench = [t["bench"] for t in tracks if t["market"] == "US" and "bench" in t]
        total_bench = round(sum(us_bench) / len(us_bench), 2) if us_bench else None
        return jsonify({"tracks": tracks, "holdings": holds, "total_cap": total_cap,
                        "total_eval": total_eval, "total_pnl": total_eval - total_cap,
                        "total_ret": total_ret, "total_bench": total_bench})

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
<meta name="theme-color" content="#0e1116">
<style>
:root{--bg:#0e1116;--panel:#161b22;--panel-2:#1c232d;--line:#26303c;--ink:#e6edf3;--ink-dim:#8b98a5;--ink-faint:#5b6570;--up:#3fb950;--down:#f85149;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Malgun Gothic',-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--ink);padding:20px 14px 44px;max-width:960px;margin:0 auto}
.num{font-family:var(--mono);letter-spacing:-.02em} .up{color:var(--up)} .down{color:var(--down)}
header{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:16px;border-bottom:1px solid var(--line);margin-bottom:18px;flex-wrap:wrap;gap:8px}
.title{font-size:19px;font-weight:700} .subtitle{font-size:12px;color:var(--ink-faint);margin-top:3px}
.asof{font-size:12px;color:var(--ink-dim);text-align:right} .asof .t{font-family:var(--mono);color:var(--ink);font-size:13px}
.total{display:flex;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:20px}
.total>div{flex:1;padding:13px 15px;border-right:1px solid var(--line)} .total>div:last-child{border-right:0}
.total .k{font-size:11px;color:var(--ink-dim);margin-bottom:5px} .total .v{font-size:18px;font-weight:700} .total .v.sub{font-size:14px}
.total .v .mkt{font-size:12px;color:var(--ink-dim);font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:11px} @media(max-width:600px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.spine{height:3px;width:100%} .cbody{padding:13px 15px 14px}
.chead{display:flex;justify-content:space-between;align-items:center}
.label{font-size:14px;font-weight:700} .method{font-size:11px;color:var(--ink-dim);margin-top:2px}
.state{font-size:10px;padding:2px 7px;border-radius:5px;font-weight:700}
.state.halt{background:rgba(248,81,73,.16);color:var(--down)} .state.pause{background:rgba(139,152,165,.16);color:var(--ink-dim)} .state.ok{background:rgba(63,185,80,.14);color:var(--up)}
.ret{font-size:23px;font-weight:800;margin:10px 0 2px;display:flex;align-items:baseline;flex-wrap:wrap;gap:6px}
.mkt{font-size:13px;font-weight:600;color:var(--ink-dim);font-family:var(--mono)} .mkt b{font-weight:700}
.edge{display:inline-block;margin-top:5px;font-size:11px;font-weight:700;padding:2px 7px;border-radius:6px;font-family:var(--mono)}
.edge.win{background:rgba(63,185,80,.14);color:var(--up)} .edge.lose{background:rgba(248,81,73,.14);color:var(--down)} .edge.warn{background:rgba(139,152,165,.15);color:var(--ink-dim)}
.evalline{font-size:12px;color:var(--ink-dim);margin-top:7px} .evalline .num{color:var(--ink)}
.meta{display:flex;gap:14px;margin-top:10px;padding-top:10px;border-top:1px solid var(--line)}
.meta div{font-size:11px;color:var(--ink-faint)} .meta div b{display:block;font-size:13px;color:var(--ink);margin-top:2px}
.compare,.holds{margin-top:22px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.compare{padding:16px 15px} .compare h2,.holds h2{font-size:13px;font-weight:700;color:var(--ink)}
.compare h2{margin-bottom:14px} .holds h2{padding:15px 15px 11px}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.bar-label{width:130px;font-size:12px;flex-shrink:0} .bar-track{flex:1;height:20px;background:var(--panel-2);border-radius:5px;position:relative;overflow:hidden}
.bar-zero{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--ink-faint);opacity:.5}
.bar-fill{position:absolute;top:0;bottom:0;border-radius:4px;opacity:.9} .bar-val{width:54px;text-align:right;font-size:12px;font-weight:700}
.holds{overflow:hidden} table{width:100%;border-collapse:collapse}
th,td{text-align:right;padding:8px 15px;font-size:12px;border-top:1px solid var(--line)} th{color:var(--ink-faint);font-size:11px}
td:first-child,th:first-child{text-align:left}
.chip{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;vertical-align:middle}
.note{margin-top:16px;padding:11px 13px;background:var(--panel);border:1px solid var(--line);border-radius:9px;font-size:11px;color:var(--ink-dim);line-height:1.6}
button{background:var(--panel-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 14px;cursor:pointer;font-size:13px}
footer{margin-top:16px;font-size:11px;color:var(--ink-faint);text-align:center;line-height:1.6}
</style></head>
<body>
<header>
  <div><div class="title">nomad_stock 펀더멘털 6트랙</div>
  <div class="subtitle">필터 강도 비교 · 미국 A/B/C · 한국 1/2/3</div></div>
  <div class="asof">기준 시각<br><span class="t" id="asof">-</span></div>
</header>
<div class="total">
  <div><div class="k">총 투입원금</div><div class="v num" id="tcap">-</div></div>
  <div><div class="k">총 평가액</div><div class="v num" id="teval">-</div></div>
  <div><div class="k">총 손익 (vs 시장)</div><div class="v sub num" id="tpnl">-</div></div>
</div>
<div class="grid" id="cards"></div>
<div class="compare"><h2>트랙별 수익률 비교 <span style="font-weight:500;color:var(--ink-faint)">— 괄호는 시작 후 시장</span></h2><div id="bars"></div></div>
<div class="holds"><h2>보유 종목 (전 트랙)</h2>
  <table><thead><tr><th>종목</th><th>트랙</th><th>매수가</th><th>현재가</th><th>수익률</th></tr></thead>
  <tbody id="holdrows"></tbody></table></div>
<div class="note">📌 6트랙 모두 페이퍼(모의). 변수는 <b>필터 강도(엄선·균형·폭넓게)</b> 하나 — 같은 전략을 강도만 달리해 비교합니다. 손절 없음(장기), 트랙별 방어선 −15%. 한국 벤치마크(코스피200)는 무료 데이터라 참고용 ⚠. 매매·재개는 텔레그램 봇으로.</div>
<div style="margin:16px 0"><button onclick="load()">새로고침</button>
  {% if auth %}<a href="/logout" style="color:#8b98a5;font-size:12px;margin-left:12px">로그아웃</a>{% endif %}</div>
<footer>기존 추종·구펀더·역추세 트랙은 아카이브(비활성)로 보존 · 텔레그램 '아카이브'로 조회</footer>
<script>
const won=n=>Math.round(n).toLocaleString('ko-KR');
const sg=v=>(v>=0?'+':'')+Number(v).toFixed(1);
function stateBadge(t){
  if(t.paused) return '<span class="state pause">⏸ 쉬기</span>';
  if(t.halted) return '<span class="state halt">🔴 정지</span>';
  return '<span class="state ok">🟢</span>';
}
function mktHtml(t){
  if(t.bench==null) return '';
  const cls=t.bench>=0?'up':'down', warn=t.bench_warn?' ⚠':'';
  return `<span class="mkt">(${t.bench_name} <b class="${cls}">${sg(t.bench)}%</b>${warn})</span>`;
}
function edgeHtml(t){
  if(t.edge==null) return '';
  if(t.bench_warn) return `<span class="edge warn">${t.bench_name} ⚠ 참고용</span>`;
  const win=t.edge>=0;
  return `<span class="edge ${win?'win':'lose'}">${sg(t.edge)}%p ${win?'선방':'뒤짐'}</span>`;
}
async function load(){
  let d; try{ d=await (await fetch('/api/all')).json(); }catch(e){ document.getElementById('asof').textContent='조회실패'; return; }
  document.getElementById('asof').textContent=new Date().toLocaleString('ko-KR',{hour12:false}).slice(5);
  document.getElementById('tcap').textContent=won(d.total_cap)+'원';
  document.getElementById('teval').textContent=won(d.total_eval)+'원';
  const tp=document.getElementById('tpnl');
  const mkt=(d.total_bench!=null)?` <span class="mkt">(시장 ${sg(d.total_bench)}%)</span>`:'';
  tp.innerHTML=sg(d.total_ret)+'%'+mkt;
  tp.className='v sub num '+(d.total_ret>=0?'up':'down');
  const cw=document.getElementById('cards'); cw.innerHTML='';
  d.tracks.forEach(t=>{ const col=t.color;
    let inner;
    if(t.error){
      inner=`<div class="ret"><span class="down" style="font-size:16px">조회 실패</span></div>`;
    }else{
      const rcls=t.ret>=0?'up':'down';
      inner=`<div class="ret"><span class="num ${rcls}">${sg(t.ret)}%</span>${mktHtml(t)}</div>
        ${edgeHtml(t)}
        <div class="evalline">평가 <span class="num">${won(t.eval)}원</span> · 원금 <span class="num">${won(t.cap)}원</span></div>
        <div class="meta"><div>보유<b class="num">${t.holds}종목</b></div><div>현금<b class="num">${Math.round(t.cash_pct)}%</b></div></div>`;
    }
    cw.innerHTML+=`<div class="card"><div class="spine" style="background:${col}"></div><div class="cbody">
      <div class="chead"><div><div class="label">${t.flag} ${t.label}</div><div class="method">${t.market==='US'?'미국·S&P500':'한국·코스피200'} · 3박자</div></div>${stateBadge(t)}</div>
      ${inner}</div></div>`;
  });
  const valid=d.tracks.filter(t=>!t.error), mx=Math.max(5,...valid.map(t=>Math.abs(t.ret)));
  const bw=document.getElementById('bars'); bw.innerHTML='';
  d.tracks.forEach(t=>{ const col=t.color;
    if(t.error){ bw.innerHTML+=`<div class="bar-row"><div class="bar-label"><span class="chip" style="background:${col}"></span>${t.label}</div><div class="bar-track"></div><div class="bar-val">-</div></div>`; return; }
    const w=Math.abs(t.ret)/mx*48, left=t.ret>=0?50:50-w;
    const sub=(t.bench!=null)?`<br><small style="font-size:10px;color:var(--ink-faint)">(${t.bench_name} ${sg(t.bench)}%${t.bench_warn?' ⚠':''})</small>`:'';
    bw.innerHTML+=`<div class="bar-row"><div class="bar-label"><span class="chip" style="background:${col}"></span>${t.flag} ${t.label}${sub}</div>
      <div class="bar-track"><div class="bar-zero"></div><div class="bar-fill" style="left:${left}%;width:${w}%;background:${col}"></div></div>
      <div class="bar-val ${t.ret>=0?'up':'down'}">${sg(t.ret)}%</div></div>`;
  });
  const hr=document.getElementById('holdrows'); hr.innerHTML='';
  if(!d.holdings.length){ hr.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--ink-faint);padding:16px">보유 종목 없음 (편입 전)</td></tr>'; }
  d.holdings.forEach(h=>{ hr.innerHTML+=`<tr><td><span class="chip" style="background:${h.color}"></span>${h.name}</td>
    <td>${h.flag}</td><td class="num">${h.buy}</td><td class="num">${h.cur}</td>
    <td class="num ${h.pct>=0?'up':'down'}">${sg(h.pct)}%</td></tr>`; });
}
load(); setInterval(load, 30000);
</script>
</body></html>
"""
