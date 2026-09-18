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


def create_app(enable_scheduler: bool = False) -> Flask:
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

    _all_cache = {"data": None, "ts": 0.0}

    def _compute_all():
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
                t["cash"] = round(e["cash_krw"])
                t["bench"] = round(e["bench_ret"], 2)
                t["edge"] = round(e["excess"], 2)
                t["halted"] = e["halted"]
                t["paused"] = e["paused"]
                for r in e["rows"]:
                    holds.append({"name": r["name"], "symbol": r["symbol"], "track": tid,
                                  "flag": meta["flag"], "color": meta["color"], "buy": r["buy_disp"],
                                  "cur": r["cur_disp"], "pct": round(r["pct"], 1)})
            except Exception as ex:
                t["cap"] = 10_000_000
                t["error"] = str(ex)[:60]
            tracks.append(t)

        total_cap = sum(t.get("cap", 10_000_000) for t in tracks)
        total_eval = sum(t.get("eval", t.get("cap", 10_000_000)) for t in tracks)
        total_cash = sum(t.get("cash", 0) for t in tracks)
        total_ret = round((total_eval / total_cap - 1) * 100, 2) if total_cap else 0
        # 총 시장평균: 신뢰 가능한 미국(S&P500)만
        us_bench = [t["bench"] for t in tracks if t["market"] == "US" and "bench" in t]
        total_bench = round(sum(us_bench) / len(us_bench), 2) if us_bench else None
        return {"tracks": tracks, "holdings": holds, "total_cap": total_cap,
                "total_eval": total_eval, "total_pnl": total_eval - total_cap,
                "total_cash": total_cash,
                "total_ret": total_ret, "total_bench": total_bench}

    @app.route("/api/all")
    @login_required
    def api_all():
        # 백그라운드로 미리 계산된 결과를 즉시 반환(느린 콜드 조회를 화면에 안 물림).
        if _all_cache["data"] is not None:
            return jsonify(_all_cache["data"])
        try:
            import time as _t
            _all_cache["data"] = _compute_all()
            _all_cache["ts"] = _t.time()
            return jsonify(_all_cache["data"])
        except Exception as e:
            return jsonify({"loading": True, "msg": str(e)[:80]})

    def _refresh_all():
        try:
            _all_cache["data"] = _compute_all()
        except Exception as e:
            print(f"[all-refresh] 오류: {e!r}")

    # ===== 웹앱 통합: 웹 푸시 + 실행 액션 (텔레그램 대체) =====
    @app.route("/sw.js")
    def service_worker():
        return app.response_class(_SW_JS, mimetype="application/javascript")

    @app.route("/api/push/key")
    @login_required
    def push_key():
        from . import push
        return app.response_class(push.public_key(), mimetype="text/plain")

    @app.route("/api/push/subscribe", methods=["POST"])
    @login_required
    def push_subscribe():
        from . import push
        push.add_sub(request.get_json(force=True))
        return jsonify({"ok": True, "count": push.sub_count()})

    @app.route("/api/push/test", methods=["POST"])
    @login_required
    def push_test():
        from . import push
        return jsonify({"ok": True, "sent": push.send_push("nomad_stock", "웹 알림이 정상 작동해요! 🎉", "/")})

    @app.route("/api/pending")
    @login_required
    def api_pending():
        from . import actions
        return jsonify(actions.pending())

    @app.route("/api/scan", methods=["POST"])
    @login_required
    def api_scan():
        # 스캔은 무거우므로 '별도 프로세스'로 실행(웹 응답이 안 밀리게). UI는 /api/pending 폴링.
        import subprocess
        import sys
        market = request.args.get("market", "BOTH")
        subprocess.Popen([sys.executable, "-m", "nomad_stock.web.scan_job", market], cwd=_ROOT)
        return jsonify({"ok": True, "started": True, "market": market})

    @app.route("/api/lowwatch", methods=["POST"])
    @login_required
    def api_lowwatch():
        import threading

        from . import actions
        threading.Thread(target=lambda: actions.run_low_watch(client), daemon=True).start()
        return jsonify({"ok": True, "started": True})

    @app.route("/api/buy", methods=["POST"])
    @login_required
    def api_buy():
        from . import actions
        d = request.get_json(force=True)
        r = actions.buy(d.get("tid"), d.get("symbol"))
        _refresh_all()
        return jsonify(r)

    @app.route("/api/sell", methods=["POST"])
    @login_required
    def api_sell():
        from . import actions
        d = request.get_json(force=True)
        r = actions.sell(d.get("tid"), d.get("symbol"))
        _refresh_all()
        return jsonify(r)

    @app.route("/api/track-action", methods=["POST"])
    @login_required
    def api_track_action():
        from . import actions
        d = request.get_json(force=True)
        act, tid = d.get("action"), d.get("tid")
        if act == "resume":
            r = actions.resume(tid)
        elif act == "pause":
            r = actions.pause(tid)
        elif act == "drop":
            r = actions.drop(tid, d.get("symbol"))
        else:
            return jsonify({"ok": False, "msg": "알 수 없는 동작"})
        _refresh_all()
        return jsonify(r)

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

    if enable_scheduler:
        import threading
        import time as _t

        def _refresher():
            while True:
                _refresh_all()   # /api/all 결과를 60초마다 미리 계산해 즉시 응답 가능하게
                _t.sleep(60)

        threading.Thread(target=_refresher, daemon=True, name="all-refresher").start()
        from . import scheduler
        scheduler.start()
    return app


_SW_JS = """
self.addEventListener('push', function(e){
  var d = {}; try { d = e.data ? e.data.json() : {}; } catch(_) {}
  e.waitUntil(self.registration.showNotification(d.title || 'nomad_stock', {
    body: d.body || '', icon: '/static/icon-192.png', badge: '/static/icon-192.png',
    data: { url: d.url || '/' }, vibrate: [80,40,80]
  }));
});
self.addEventListener('notificationclick', function(e){
  e.notification.close();
  e.waitUntil(clients.matchAll({type:'window'}).then(function(ws){
    for (var i=0;i<ws.length;i++){ if(ws[i].url.indexOf(self.location.origin)===0 && 'focus' in ws[i]) return ws[i].focus(); }
    if (clients.openWindow) return clients.openWindow(e.notification.data.url || '/');
  }));
});
"""


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
body{font-family:'Malgun Gothic',-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--ink);padding:18px 14px 44px;max-width:960px;margin:0 auto}
.num{font-family:var(--mono);letter-spacing:-.02em} .up{color:var(--up)} .down{color:var(--down)}
header{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:12px;border-bottom:1px solid var(--line);flex-wrap:wrap;gap:8px}
.title{font-size:19px;font-weight:700} .subtitle{font-size:12px;color:var(--ink-faint);margin-top:3px}
.asof{font-size:12px;color:var(--ink-dim);text-align:right} .asof .t{font-family:var(--mono);color:var(--ink);font-size:13px}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0 18px}
button{background:var(--panel-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 13px;cursor:pointer;font-size:13px}
button:active{transform:scale(.97)} button.on{background:rgba(63,185,80,.16);border-color:var(--up);color:var(--up)}
.tmsg{font-size:12px;color:var(--ink-dim)}
.total{display:flex;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:20px}
.total>div{flex:1 1 130px;padding:13px 15px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)} .total>div:last-child{border-right:0}
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
.cand{margin-top:10px;padding-top:10px;border-top:1px dashed var(--line)}
.cand-h{font-size:11px;color:var(--ink-faint);margin-bottom:6px}
.cand-row{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:5px;font-size:12px}
.cand-row .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mini{font-size:11px;padding:4px 9px;border-radius:6px}
.acts{display:flex;gap:6px;margin-top:9px}
.compare,.holds{margin-top:22px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.compare{padding:16px 15px} .compare h2,.holds h2{font-size:13px;font-weight:700;color:var(--ink)}
.compare h2{margin-bottom:14px} .holds h2{padding:15px 15px 11px}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.bar-label{width:130px;font-size:12px;flex-shrink:0} .bar-track{flex:1;height:20px;background:var(--panel-2);border-radius:5px;position:relative;overflow:hidden}
.bar-zero{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--ink-faint);opacity:.5}
.bar-fill{position:absolute;top:0;bottom:0;border-radius:4px;opacity:.9} .bar-val{width:54px;text-align:right;font-size:12px;font-weight:700}
.holds{overflow:hidden} table{width:100%;border-collapse:collapse}
th,td{text-align:right;padding:8px 12px;font-size:12px;border-top:1px solid var(--line)} th{color:var(--ink-faint);font-size:11px}
td:first-child,th:first-child{text-align:left}
.chip{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;vertical-align:middle}
.note{margin-top:16px;padding:11px 13px;background:var(--panel);border:1px solid var(--line);border-radius:9px;font-size:11px;color:var(--ink-dim);line-height:1.6}
footer{margin-top:16px;font-size:11px;color:var(--ink-faint);text-align:center;line-height:1.6}
</style></head>
<body>
<header>
  <div><div class="title">nomad_stock 펀더멘털 6트랙</div>
  <div class="subtitle">필터 강도 비교 · 미국 A/B/C · 한국 1/2/3</div></div>
  <div class="asof">기준 시각<br><span class="t" id="asof">-</span></div>
</header>
<div class="toolbar">
  <button id="pushbtn" onclick="enablePush()">🔔 알림 켜기</button>
  <button onclick="doScan()">🔎 스캔</button>
  <button onclick="doLow()">📉 저가 점검</button>
  <button onclick="load()">↻ 새로고침</button>
  <span class="tmsg" id="toolmsg"></span>
</div>
<div class="total">
  <div><div class="k">총 투입원금</div><div class="v num" id="tcap">-</div></div>
  <div><div class="k">총 평가액</div><div class="v num" id="teval">-</div></div>
  <div><div class="k">총 현금(잔액)</div><div class="v num" id="tcash">-</div></div>
  <div><div class="k">총 손익 (vs 시장)</div><div class="v sub num" id="tpnl">-</div></div>
</div>
<div class="grid" id="cards"></div>
<div class="compare"><h2>트랙별 수익률 비교 <span style="font-weight:500;color:var(--ink-faint)">— 괄호는 시작 후 시장</span></h2><div id="bars"></div></div>
<div class="note">📌 6트랙 모두 페이퍼(모의). 변수는 <b>필터 강도(엄선·균형·폭넓게)</b> 하나. 손절 없음(장기), 트랙별 방어선 −15%. 한국 벤치마크(코스피200)는 무료 데이터라 참고용 ⚠. <b>스캔·편입·재개 모두 이 화면에서</b> — 알림은 🔔로 켜면 폰으로 옵니다.</div>
<div style="margin:16px 0"><a href="/logout" style="color:#8b98a5;font-size:12px">로그아웃</a></div>
<footer>기존 추종·구펀더·역추세 트랙은 아카이브(비활성)로 보존</footer>
<script>
const won=n=>Math.round(n).toLocaleString('ko-KR');
const sg=v=>(v>=0?'+':'')+Number(v).toFixed(1);
let PENDING={},HOLDINGS=[];
function toast(m){document.getElementById('toolmsg').textContent=m;}
function holdsHtml(t){
  const hs=HOLDINGS.filter(h=>h.track===t.id);
  if(!hs.length)return '<div class="cand"><div class="cand-h">📦 보유 없음 (현금 대기)</div></div>';
  let h='<div class="cand"><div class="cand-h">📦 보유 '+hs.length+'종목</div>';
  hs.forEach(x=>{h+=`<div class="cand-row"><span class="nm">${x.name} <span style="color:var(--ink-faint)">${x.buy}→${x.cur}</span></span>`
    +`<span><span class="num ${x.pct>=0?'up':'down'}" style="margin-right:6px">${sg(x.pct)}%</span><button class="mini" onclick="sell('${t.id}','${x.symbol}')">매도</button></span></div>`;});
  return h+'</div>';
}
function u8(base64){const p='='.repeat((4-base64.length%4)%4);const b=(base64+p).replace(/-/g,'+').replace(/_/g,'/');const r=atob(b);return Uint8Array.from([...r].map(c=>c.charCodeAt(0)));}
async function enablePush(){
  try{
    if(location.protocol!=='https:'&&location.hostname!=='localhost'){toast('웹 알림은 HTTPS가 필요해요 — HTTPS(클라우드플레어 터널) 설정 후 켜집니다');return;}
    if(!('serviceWorker' in navigator)||!('PushManager' in window)){toast('이 브라우저는 웹 알림 미지원');return;}
    const reg=await navigator.serviceWorker.register('/sw.js');
    const perm=await Notification.requestPermission();
    if(perm!=='granted'){toast('알림 권한이 거부됐어요 (브라우저 설정에서 허용)');return;}
    const key=await (await fetch('/api/push/key')).text();
    const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:u8(key)});
    await fetch('/api/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sub)});
    await fetch('/api/push/test',{method:'POST'});
    const b=document.getElementById('pushbtn');b.textContent='🔔 알림 켜짐';b.classList.add('on');
    toast('알림이 켜졌어요! (테스트 알림 확인)');
  }catch(e){toast('알림 켜기 실패: '+e.message);}
}
async function doScan(){toast('스캔 중… (1~2분, 끝나면 후보가 카드에 떠요)');
  await fetch('/api/scan?market=BOTH',{method:'POST'});
  let n=0;const iv=setInterval(async()=>{await load();n++;if(n>=24){clearInterval(iv);toast('스캔 완료');}},6000);}
async function doLow(){toast('저가 점검 실행 (저가 근처면 알림/표시)');await fetch('/api/lowwatch',{method:'POST'});setTimeout(load,4000);}
async function buy(tid,sym){toast('편입 중…');const r=await (await fetch('/api/buy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tid,symbol:sym})})).json();toast(r.msg||'완료');load();}
async function sell(tid,sym){if(!confirm('매도할까요?'))return;const r=await (await fetch('/api/sell',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tid,symbol:sym})})).json();toast(r.msg||'완료');load();}
async function tact(action,tid,sym){const r=await (await fetch('/api/track-action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,tid,symbol:sym})})).json();toast(r.msg||'완료');load();}
function stateBadge(t){if(t.paused)return '<span class="state pause">⏸ 쉬기</span>';if(t.halted)return '<span class="state halt">🔴 정지</span>';return '<span class="state ok">🟢</span>';}
function mktHtml(t){if(t.bench==null)return '';const cls=t.bench>=0?'up':'down',warn=t.bench_warn?' ⚠':'';return `<span class="mkt">(${t.bench_name} <b class="${cls}">${sg(t.bench)}%</b>${warn})</span>`;}
function edgeHtml(t){if(t.edge==null)return '';if(t.bench_warn)return `<span class="edge warn">${t.bench_name} ⚠ 참고용</span>`;const win=t.edge>=0;return `<span class="edge ${win?'win':'lose'}">${sg(t.edge)}%p ${win?'선방':'뒤짐'}</span>`;}
function candHtml(t){
  const cs=(PENDING[t.id]||[]);
  if(!cs.length)return '';
  const unit=t.market==='US'?'$':'원';
  let h='<div class="cand"><div class="cand-h">📗 편입 후보 '+cs.length+'</div>';
  cs.forEach(c=>{h+=`<div class="cand-row"><span class="nm">${c.name} <span style="color:var(--ink-faint)">${Number(c.price).toLocaleString()}${unit}·점수${c.score}</span></span>`
    +`<span><button class="mini" onclick="buy('${t.id}','${c.symbol}')">편입</button> <button class="mini" onclick="tact('drop','${t.id}','${c.symbol}')">✕</button></span></div>`;});
  return h+'</div>';
}
function actsHtml(t){
  let h='<div class="acts">';
  if(t.halted||t.paused)h+=`<button class="mini" onclick="tact('resume','${t.id}')">▶ 재개</button>`;
  if(!t.paused)h+=`<button class="mini" onclick="tact('pause','${t.id}')">⏸ 쉬기</button>`;
  return h+'</div>';
}
async function load(){
  let d;try{d=await (await fetch('/api/all')).json();}catch(e){document.getElementById('asof').textContent='조회실패';return;}
  if(d&&d.loading){document.getElementById('asof').textContent='불러오는 중…';setTimeout(load,4000);return;}
  try{PENDING=await (await fetch('/api/pending')).json();}catch(e){}
  document.getElementById('asof').textContent=new Date().toLocaleString('ko-KR',{hour12:false}).slice(5);
  document.getElementById('tcap').textContent=won(d.total_cap)+'원';
  document.getElementById('teval').textContent=won(d.total_eval)+'원';
  document.getElementById('tcash').textContent=won(d.total_cash||0)+'원';
  HOLDINGS=d.holdings||[];
  const tp=document.getElementById('tpnl');
  const mkt=(d.total_bench!=null)?` <span class="mkt">(시장 ${sg(d.total_bench)}%)</span>`:'';
  tp.innerHTML=sg(d.total_ret)+'%'+mkt;tp.className='v sub num '+(d.total_ret>=0?'up':'down');
  const cw=document.getElementById('cards');cw.innerHTML='';
  d.tracks.forEach(t=>{const col=t.color;let inner;
    if(t.error){inner=`<div class="ret"><span class="down" style="font-size:16px">조회 실패</span></div>`;}
    else{const rcls=t.ret>=0?'up':'down';
      inner=`<div class="ret"><span class="num ${rcls}">${sg(t.ret)}%</span>${mktHtml(t)}</div>${edgeHtml(t)}
        <div class="evalline">평가 <span class="num">${won(t.eval)}원</span> · 원금 <span class="num">${won(t.cap)}원</span></div>
        <div class="meta"><div>보유<b class="num">${t.holds}종목</b></div><div>현금<b class="num">${Math.round(t.cash_pct)}%</b></div></div>
        ${holdsHtml(t)}${candHtml(t)}${actsHtml(t)}`;}
    cw.innerHTML+=`<div class="card"><div class="spine" style="background:${col}"></div><div class="cbody">
      <div class="chead"><div><div class="label">${t.flag} ${t.label}</div><div class="method">${t.market==='US'?'미국·S&P500':'한국·코스피200'} · 3박자</div></div>${stateBadge(t)}</div>${inner}</div></div>`;});
  const valid=d.tracks.filter(t=>!t.error),mx=Math.max(5,...valid.map(t=>Math.abs(t.ret)));
  const bw=document.getElementById('bars');bw.innerHTML='';
  d.tracks.forEach(t=>{const col=t.color;
    if(t.error){bw.innerHTML+=`<div class="bar-row"><div class="bar-label"><span class="chip" style="background:${col}"></span>${t.label}</div><div class="bar-track"></div><div class="bar-val">-</div></div>`;return;}
    const w=Math.abs(t.ret)/mx*48,left=t.ret>=0?50:50-w;
    const sub=(t.bench!=null)?`<br><small style="font-size:10px;color:var(--ink-faint)">(${t.bench_name} ${sg(t.bench)}%${t.bench_warn?' ⚠':''})</small>`:'';
    bw.innerHTML+=`<div class="bar-row"><div class="bar-label"><span class="chip" style="background:${col}"></span>${t.flag} ${t.label}${sub}</div>
      <div class="bar-track"><div class="bar-zero"></div><div class="bar-fill" style="left:${left}%;width:${w}%;background:${col}"></div></div>
      <div class="bar-val ${t.ret>=0?'up':'down'}">${sg(t.ret)}%</div></div>`;});
}
load();setInterval(load,30000);
</script>
</body></html>
"""
