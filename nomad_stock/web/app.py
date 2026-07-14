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
        # 4트랙 종합 (A=한국KIS, B/C/D=페이퍼 장부). 비교 대시보드용.
        from .. import paper_long, paper_track_d, paper_us, rules
        cap = 10_000_000
        tracks, holds = [], []

        a = {"id": "A", "name": "추종 (한국)", "method": "볼린저 상단권 · 단기 · 한투 모의", "cap": cap}
        try:
            bal = client.get_balance()
            a["eval"] = bal["total_eval"]
            a["ret"] = (bal["total_eval"] / cap - 1) * 100 if cap else 0
            a["holds"] = len(bal["holdings"])
            a["cash_pct"] = bal["cash"] / bal["total_eval"] * 100 if bal["total_eval"] else 0
            for h in bal["holdings"]:
                pct = (h["cur_price"] / h["avg_price"] - 1) * 100 if h["avg_price"] else 0
                holds.append({"name": h["name"], "track": "A", "buy": f"{h['avg_price']:,}",
                              "cur": f"{h['cur_price']:,}", "pct": pct})
        except Exception as e:
            a["error"] = str(e)[:60]
        tracks.append(a)

        def paper(tid, name, method, mod, spx=False):
            t = {"id": tid, "name": name, "method": method, "cap": cap}
            try:
                e = mod.evaluate()
                t["eval"] = e["total_krw"]
                t["ret"] = e["pnl_krw"] / cap * 100 if cap else 0
                t["holds"] = len(e["rows"])
                t["cash_pct"] = e["cash_usd"] / e["total_usd"] * 100 if e["total_usd"] else 0
                if spx and "excess" in e:
                    t["excess"] = e["excess"]
                for r in e["rows"]:
                    holds.append({"name": r["name"], "track": tid, "buy": f"${r['avg_usd']}",
                                  "cur": f"${r['cur_usd']}", "pct": r["pct"]})
            except Exception as ex:
                t["error"] = str(ex)[:60]
            return t

        tracks.append(paper("B", "추종 (미국)", "볼린저 상단권 · 단기 · 페이퍼", paper_us))
        tracks.append(paper("C", "펀더멘털", "3박자 필터 · 장기 · 페이퍼", paper_long, spx=True))
        tracks.append(paper("D", "역추세", "볼린저 하단·RSI 과매도 · 페이퍼", paper_track_d))

        # ----- 트랙별 시장(벤치마크) 대비 비교 : 각 트랙 시작일 기준 -----
        try:
            starts = {
                "A": rules.TRACK_A_START,
                "B": paper_us.ledger_start(paper_us.load_ledger()),
                "C": (paper_long.load_ledger().get("start_date")
                      or paper_us.ledger_start(paper_long.load_ledger())),
                "D": paper_us.ledger_start(paper_track_d.load_ledger()),
            }
        except Exception:
            starts = {}
        bench_tk = {"A": ("KS200", "코스피200"), "B": ("US500", "S&P500"),
                    "C": ("US500", "S&P500"), "D": ("US500", "S&P500")}
        bvals = []
        for t in tracks:
            tk, bname = bench_tk[t["id"]]
            t["bench_name"] = bname
            start = starts.get(t["id"])
            if start and not t.get("error"):
                br = paper_us.index_return(tk, start)
                if br is not None:
                    t["bench"] = round(br, 2)
                    t["edge"] = round(t["ret"] - br, 2)
                    if t["id"] == "A":
                        t["bench_warn"] = True   # 한국 무료지수 데이터 불안정 → 참고용 경고
                    else:
                        bvals.append(br)         # 총 시장평균엔 신뢰 가능한 미국(S&P500)만

        total_eval = sum(t.get("eval", t["cap"]) for t in tracks)
        total_cap = sum(t["cap"] for t in tracks)
        total_ret = (total_eval / total_cap - 1) * 100 if total_cap else 0
        total_bench = round(sum(bvals) / len(bvals), 2) if bvals else None
        return jsonify({"tracks": tracks, "holdings": holds, "total_cap": total_cap,
                        "total_eval": total_eval, "total_pnl": total_eval - total_cap,
                        "total_ret": round(total_ret, 2), "total_bench": total_bench})

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
:root{--bg:#0e1116;--panel:#161b22;--panel-2:#1c232d;--line:#26303c;--ink:#e6edf3;--ink-dim:#8b98a5;--ink-faint:#5b6570;--up:#3fb950;--down:#f85149;--A:#4c8dff;--B:#5ac8fa;--C:#c07cff;--D:#ffb454;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Malgun Gothic',-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--ink);padding:20px 14px 44px;max-width:920px;margin:0 auto}
.num{font-family:var(--mono);letter-spacing:-.02em} .up{color:var(--up)} .down{color:var(--down)}
header{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:16px;border-bottom:1px solid var(--line);margin-bottom:20px;flex-wrap:wrap;gap:8px}
.title{font-size:19px;font-weight:700} .subtitle{font-size:12px;color:var(--ink-faint);margin-top:3px}
.asof{font-size:12px;color:var(--ink-dim);text-align:right} .asof .t{font-family:var(--mono);color:var(--ink);font-size:13px}
.total{display:flex;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:22px}
.total>div{flex:1;padding:14px 16px;border-right:1px solid var(--line)} .total>div:last-child{border-right:0}
.total .k{font-size:11px;color:var(--ink-dim);margin-bottom:6px} .total .v{font-size:20px;font-weight:700} .total .v.sub{font-size:15px}
.total .v .mkt{font-size:13px;color:var(--ink-dim);font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media(max-width:560px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.spine{height:3px;width:100%} .cbody{padding:15px 16px 16px}
.tag{font-size:10px;font-weight:700;letter-spacing:.04em;padding:2px 7px;border-radius:5px;color:#0e1116}
.card h3{font-size:15px;font-weight:700;margin-top:9px} .method{font-size:11px;color:var(--ink-dim);margin-top:2px}
.ret{font-size:26px;font-weight:800;margin:12px 0 2px;display:flex;align-items:baseline;flex-wrap:wrap;gap:7px}
.mkt{font-size:14px;font-weight:600;color:var(--ink-dim);font-family:var(--mono)} .mkt b{font-weight:700}
.edge{display:inline-block;margin-top:6px;font-size:12px;font-weight:700;padding:3px 8px;border-radius:6px;font-family:var(--mono)}
.edge.win{background:rgba(63,185,80,.14);color:var(--up)} .edge.lose{background:rgba(248,81,73,.14);color:var(--down)}
.edge.warn{background:rgba(139,152,165,.15);color:var(--ink-dim)}
.evalline{font-size:12px;color:var(--ink-dim);margin-top:8px} .evalline .num{color:var(--ink)}
.bench{font-size:11px;margin-top:9px;color:var(--ink-dim)}
.meta{display:flex;gap:14px;margin-top:12px;padding-top:12px;border-top:1px solid var(--line);flex-wrap:wrap}
.meta div{font-size:11px;color:var(--ink-faint)} .meta div b{display:block;font-size:13px;color:var(--ink);margin-top:2px}
.compare,.holds{margin-top:24px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.compare{padding:18px 16px} .compare h2,.holds h2{font-size:13px;font-weight:700;color:var(--ink)}
.compare h2{margin-bottom:16px} .compare h2 span{font-weight:500;color:var(--ink-faint)} .holds h2{padding:16px 16px 12px}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.bar-label{width:120px;font-size:12px;flex-shrink:0;line-height:1.35} .bar-label small{font-size:10px;color:var(--ink-faint);font-family:var(--mono)}
.bar-track{flex:1;height:22px;background:var(--panel-2);border-radius:5px;position:relative;overflow:hidden}
.bar-zero{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--ink-faint);opacity:.5}
.bar-fill{position:absolute;top:0;bottom:0;border-radius:4px;opacity:.9} .bar-val{width:60px;text-align:right;font-size:13px;font-weight:700;flex-shrink:0}
.holds{overflow:hidden} table{width:100%;border-collapse:collapse}
th,td{text-align:right;padding:9px 16px;font-size:12px;border-top:1px solid var(--line)} th{color:var(--ink-faint);font-size:11px}
td:first-child,th:first-child{text-align:left}
.chip{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;vertical-align:middle}
.badge{font-size:10px;padding:1px 5px;border-radius:4px;background:var(--panel-2);color:var(--ink-dim)}
.note{margin-top:16px;padding:11px 13px;background:rgba(255,180,84,.07);border:1px solid rgba(255,180,84,.22);border-radius:9px;font-size:11px;color:var(--D);line-height:1.5}
button{background:var(--panel-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 14px;cursor:pointer;font-size:13px}
footer{margin-top:18px;font-size:11px;color:var(--ink-faint);text-align:center;line-height:1.6}
</style></head>
<body>
<header>
  <div><div class="title">nomad_stock 페이퍼 대시보드</div>
  <div class="subtitle">4개 전략 비교 · 같은 장세, 다른 기법</div></div>
  <div class="asof">기준 시각<br><span class="t" id="asof">-</span></div>
</header>
<div class="total">
  <div><div class="k">총 투입원금</div><div class="v num" id="tcap">-</div></div>
  <div><div class="k">총 평가액</div><div class="v num" id="teval">-</div></div>
  <div><div class="k">총 손익 (vs 시장)</div><div class="v sub num" id="tpnl">-</div></div>
</div>
<div class="grid" id="cards"></div>
<div class="compare"><h2>전략별 수익률 비교 <span>— 괄호는 각 트랙 시작 후 시장 평균</span></h2><div id="bars"></div></div>
<div class="holds"><h2>보유 종목 (전 트랙)</h2>
  <table><thead><tr><th>종목</th><th>트랙</th><th>매수가</th><th>현재가</th><th>수익률</th><th>구분</th></tr></thead>
  <tbody id="holdrows"></tbody></table></div>
<div class="note">📌 벤치마크는 <b>각 트랙이 실제 매매를 시작한 날</b>부터 지수 수익률입니다. 한국 지수는 무료 데이터라 가끔 값이 튈 수 있어 <b>참고용</b>으로만 보세요.</div>
<div style="margin:16px 0"><button onclick="load()">새로고침</button>
  {% if auth %}<a href="/logout" style="color:#8b98a5;font-size:12px;margin-left:12px">로그아웃</a>{% endif %}</div>
<footer>A·B·C·D 모두 페이퍼/모의 · 실제 돈 안 걸림<br>손절 -7% 자동 · 한 종목 20% · 누적 -100만원 방어선</footer>
<script>
const C={A:'#4c8dff',B:'#5ac8fa',C:'#c07cff',D:'#ffb454'};
const won=n=>Math.round(n).toLocaleString('ko-KR');
const sg=v=>(v>=0?'+':'')+v.toFixed(1);
function edgeHtml(t){
  if(t.edge==null) return '';
  if(t.bench_warn) return `<span class="edge warn">⚠ ${t.bench_name} 데이터 불안정 · 비교 참고불가</span>`;
  const win=t.edge>=0;
  return `<span class="edge ${win?'win':'lose'}">${sg(t.edge)}%p ${win?'선방':'뒤짐'}</span>`;
}
function mktHtml(t){
  if(t.bench==null) return '<span class="mkt">(시장 데이터 없음)</span>';
  const cls=t.bench>=0?'up':'down';
  const warn=t.bench_warn?' ⚠':'';
  return `<span class="mkt">(${t.bench_name} <b class="${cls}">${sg(t.bench)}%</b>${warn})</span>`;
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
  d.tracks.forEach(t=>{ const col=C[t.id];
    let inner;
    if(t.error){
      inner=`<div class="ret"><span class="down" style="font-size:18px">KIS 조회 실패</span></div>
        <div class="evalline down">잠시 후 다시 시도됩니다</div>`;
    }else{
      const rcls=t.ret>=0?'up':'down';
      const bench=(t.excess!=null)?`<div class="bench">S&P500 대비 초과 <b class="num ${t.excess>=0?'up':'down'}">${sg(t.excess)}%p</b></div>`:'';
      inner=`<div class="ret"><span class="num ${rcls}">${sg(t.ret)}%</span>${mktHtml(t)}</div>
        ${edgeHtml(t)}
        <div class="evalline">평가 <span class="num">${won(t.eval)}원</span> · 원금 <span class="num">${won(t.cap)}원</span></div>
        ${bench}
        <div class="meta"><div>보유<b class="num">${t.holds}종목</b></div><div>현금<b class="num">${Math.round(t.cash_pct)}%</b></div></div>`;
    }
    cw.innerHTML+=`<div class="card"><div class="spine" style="background:${col}"></div><div class="cbody">
      <span class="tag" style="background:${col}">${t.id}</span><h3>${t.name}</h3><div class="method">${t.method}</div>
      ${inner}</div></div>`;
  });
  const valid=d.tracks.filter(t=>!t.error), mx=Math.max(5,...valid.map(t=>Math.abs(t.ret)));
  const bw=document.getElementById('bars'); bw.innerHTML='';
  d.tracks.forEach(t=>{ const col=C[t.id];
    const sub=(t.bench!=null)?`<br><small>(${t.bench_name} ${sg(t.bench)}%${t.bench_warn?' ⚠':''})</small>`:'';
    if(t.error){ bw.innerHTML+=`<div class="bar-row"><div class="bar-label"><span class="chip" style="background:${col}"></span>${t.id} ${t.name}</div><div class="bar-track"></div><div class="bar-val">-</div></div>`; return; }
    const w=Math.abs(t.ret)/mx*48, left=t.ret>=0?50:50-w;
    bw.innerHTML+=`<div class="bar-row"><div class="bar-label"><span class="chip" style="background:${col}"></span>${t.id} ${t.name}${sub}</div>
      <div class="bar-track"><div class="bar-zero"></div><div class="bar-fill" style="left:${left}%;width:${w}%;background:${col}"></div></div>
      <div class="bar-val ${t.ret>=0?'up':'down'}">${sg(t.ret)}%</div></div>`;
  });
  const hr=document.getElementById('holdrows'); hr.innerHTML='';
  if(!d.holdings.length){ hr.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--ink-faint);padding:16px">보유 종목 없음</td></tr>'; }
  d.holdings.forEach(h=>{ hr.innerHTML+=`<tr><td><span class="chip" style="background:${C[h.track]}"></span>${h.name}</td>
    <td>${h.track}</td><td class="num">${h.buy}</td><td class="num">${h.cur}</td>
    <td class="num ${h.pct>=0?'up':'down'}">${sg(h.pct)}%</td><td><span class="badge">봇</span></td></tr>`; });
}
load(); setInterval(load, 30000);
</script>
</body></html>
"""
