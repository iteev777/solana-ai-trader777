import hmac
import os
import threading
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

import database

app = Flask(__name__)
_server = None
_server_thread = None
_server_lock = threading.Lock()


def _authorized():
    expected = os.getenv("TROJAN_DASHBOARD_TOKEN", "")
    if not expected:
        return False
    auth = request.authorization
    supplied = auth.password if auth and auth.password else ""
    return bool(supplied) and hmac.compare_digest(supplied, expected)


@app.before_request
def require_dashboard_auth():
    if not _authorized():
        return Response(
            "Dashboard authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Trojan V2 Dashboard"'}
        )


def _rows(query, params=()):
    with database.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(query, params).fetchall()
        ]


def _runtime():
    try:
        import main
        return {
            "pending_analyses": len(main.pending_tasks),
            "discovery_pending": len(main.pending_discovery_tokens),
            "sniper_pending": len(main.pending_sniper_tokens),
            "seen_tokens": len(main.seen_tokens),
            "last_scan_time": main.last_scan_time,
            "position_check_interval": main.POSITION_CHECK_INTERVAL,
            "scan_interval": main.SCAN_INTERVAL,
        }
    except Exception as error:
        return {"runtime_status": "unavailable", "error": str(error)[:180]}


def _positions():
    import paper_trading as paper
    import main

    result = []
    for address, position in paper.get_positions().items():
        entry = float(position.get("entry_price") or 0)
        current = main.last_prices.get(address)
        current = float(current) if current else None
        roi = ((current / entry) - 1) * 100 if current and entry > 0 else None
        result.append({
            "token": address,
            "strategy": position.get("strategy", "main"),
            "entry_price": entry,
            "current_price": current,
            "invested": float(position.get("invested") or 0),
            "roi_percent": round(roi, 2) if roi is not None else None,
            "opened_at": position.get("opened_at"),
            "peak_price": (position.get("metadata") or {}).get("peak_price"),
            "dca_count": int((position.get("metadata") or {}).get("dca_count", 0) or 0),
            "partial_take_profit_done": bool((position.get("metadata") or {}).get("partial_take_profit_done")),
        })
    return result



@app.get("/manifest.webmanifest")
def dashboard_manifest():
    return jsonify({
        "name": "Trojan V2 Command Center",
        "short_name": "Trojan V2",
        "description": "Solana trading bot monitoring dashboard",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#080d18",
        "theme_color": "#080d18",
        "orientation": "any",
        "icons": [{"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}]
    })


@app.get("/app-icon.svg")
def dashboard_app_icon():
    return Response(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" fill="#080d18"/><path d="M118 128h276v58H292v198h-72V186h-102z" fill="#39e6a5"/><path d="M142 330l75-78 52 48 100-112" fill="none" stroke="#77aaff" stroke-width="24" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        mimetype="image/svg+xml"
    )


@app.get("/sw.js")
def dashboard_service_worker():
    script = """
const CACHE = "trojan-dashboard-v1";
const SHELL = ["/", "/manifest.webmanifest", "/app-icon.svg"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (event.request.method !== "GET") return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE).then(cache => cache.put(event.request, copy));
    }
    return response;
  }).catch(() => caches.match(event.request)));
});
"""
    return Response(script, mimetype="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/")
def dashboard_home():
    return Response(_HTML, mimetype="text/html")


@app.get("/api/overview")
def api_overview():
    import trading_manager as manager
    import risk_manager
    from api_client import get_api_status
    from market_data import get_market_data_status

    account = manager.get_account_summary("paper")
    stats = database.get_trade_stats("paper")
    positions = _positions()

    with database.connect() as connection:
        trade_count = connection.execute(
            "SELECT COUNT(*) FROM trades WHERE mode='paper'"
        ).fetchone()[0]
        event_count = connection.execute(
            "SELECT COUNT(*) FROM bot_events"
        ).fetchone()[0]

    return jsonify({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER TRADING",
        "live_execution": "LOCKED",
        "account": account,
        "stats": stats,
        "risk": risk_manager.get_risk_summary(),
        "positions": positions,
        "position_count": len(positions),
        "trade_rows": trade_count,
        "event_rows": event_count,
        "runtime": _runtime(),
        "market_data": get_market_data_status(),
        "api": get_api_status(),
    })


@app.get("/api/trades")
def api_trades():
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    return jsonify(_rows(
        """SELECT id, token_address, action, price, amount, profit_loss,
                  timestamp, strategy
           FROM trades WHERE mode='paper'
           ORDER BY id DESC LIMIT ?""",
        (limit,)
    ))


@app.get("/api/events")
def api_events():
    limit = min(max(request.args.get("limit", 80, type=int), 1), 250)
    return jsonify(_rows(
        """SELECT id, event_type, message, token_address, timestamp
           FROM bot_events ORDER BY id DESC LIMIT ?""",
        (limit,)
    ))


@app.get("/api/strategies")
def api_strategies():
    from strategy_rules import get_strategy_rules_status
    return jsonify(get_strategy_rules_status())


@app.post("/api/strategies")
def api_save_strategy():
    if request.headers.get("X-Trojan-Action") != "1":
        return jsonify({"error": "Missing action header"}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    strategy = payload.get("strategy")
    rules = payload.get("rules")
    if strategy not in ("main", "sniper"):
        return jsonify({"error": "Unknown strategy"}), 400
    try:
        from strategy_rules import validate_rules_config
        if rules == {}:
            database.delete_strategy_config(strategy)
        else:
            validate_rules_config({strategy: rules})
            database.save_strategy_config(strategy, rules)
        return jsonify({"saved": True, "strategy": strategy})
    except Exception as error:
        return jsonify({"saved": False, "error": str(error)[:300]}), 400


@app.get("/api/readiness")
def api_readiness():
    import service_health
    from solana_onchain import get_onchain_status
    from indicators import get_indicator_status
    from copy_trade import get_copy_monitor_status
    return jsonify({
        "readiness": service_health.get_readiness_report(),
        "services": service_health.get_service_status(),
        "onchain": get_onchain_status(),
        "indicators": get_indicator_status(),
        "copy_monitor": get_copy_monitor_status()
    })


@app.get("/api/copy-trades")
def api_copy_trades():
    return jsonify(database.get_recent_copy_trade_events(100))


@app.get("/api/filtering")
def api_filtering():
    return jsonify({
        "summary": database.get_filtering_summary(),
        "recent": database.get_recent_filter_events(100)
    })


@app.get("/api/performance")
def api_performance():
    # Count closed paper trades only. BUY rows are not independent outcomes.
    rows = _rows("""
        SELECT strategy,
               SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) AS closed_trades,
               SUM(CASE WHEN action='SELL' AND profit_loss > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN action='SELL' AND profit_loss <= 0 THEN 1 ELSE 0 END) AS losses,
               COALESCE(SUM(CASE WHEN action='SELL' THEN profit_loss ELSE 0 END), 0) AS realized_pnl
        FROM trades
        WHERE mode='paper'
        GROUP BY strategy
        ORDER BY strategy
    """)
    for row in rows:
        closed = int(row.get("closed_trades") or 0)
        wins = int(row.get("wins") or 0)
        row["win_rate"] = round((wins / closed) * 100, 2) if closed else 0.0
    return jsonify({
        "strategies": rows,
        "mode": "paper",
        "live_execution": "locked"
    })


@app.get("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "database": os.path.basename(database.DATABASE),
        "live_execution": "locked",
        "dashboard": "read-only",
    })


_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#080d18"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trojan V2"><link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/app-icon.svg"><title>Trojan V2 Command Center</title>
<style>
:root{color-scheme:dark;--bg:#090e1b;--panel:#111a2b;--line:#24324a;--muted:#91a0b8;--text:#edf3ff;--green:#37d6a0;--red:#ff6d7a}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#14213a 0,var(--bg) 42%);color:var(--text);font:14px Inter,system-ui,-apple-system,sans-serif}
header{padding:22px clamp(16px,4vw,42px);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
h1{font-size:20px;margin:0;letter-spacing:.04em}.sub{color:var(--muted);font-size:12px;margin-top:5px}
.pill{border:1px solid #236b58;color:var(--green);padding:7px 11px;border-radius:30px;font-size:11px;font-weight:700;letter-spacing:.08em}
main{max-width:1400px;margin:auto;padding:22px clamp(12px,3vw,34px) 50px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.panel,.metric{background:linear-gradient(145deg,#131e31,#0f1727);border:1px solid var(--line);border-radius:14px}
.metric{padding:16px}.metric small{color:var(--muted);display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em}.metric strong{display:block;font-size:25px;margin-top:9px;font-variant-numeric:tabular-nums}
.section{margin-top:18px;padding:17px}.section h2{font-size:14px;margin:0 0 14px;letter-spacing:.04em}
.split{display:grid;grid-template-columns:1.2fr .8fr;gap:12px;margin-top:12px}
table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;color:var(--muted);font-weight:600;padding:9px 8px;border-bottom:1px solid var(--line)}td{padding:10px 8px;border-bottom:1px solid #1b2940;vertical-align:top}
.token{max-width:210px;overflow-wrap:anywhere;color:#b9d3ff}.good{color:var(--green)}.bad{color:var(--red)}.muted{color:var(--muted)}
.health{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.health div{padding:11px;background:#0b1423;border:1px solid var(--line);border-radius:9px}.health b{display:block;font-size:12px;margin-bottom:5px}
.actions{display:flex;align-items:center;gap:10px}.refresh{background:#1c3152;border:1px solid #35527c;color:var(--text);padding:8px 12px;border-radius:8px}
footer{color:var(--muted);font-size:11px;text-align:center;padding:24px}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.split{grid-template-columns:1fr}}
@media(max-width:480px){.metric strong{font-size:21px}.section{padding:12px}table{font-size:11px}}
/* Mobile app shell */
:root{--bg:#080d18;--panel:#101a2b;--line:#22334d}
body{background:radial-gradient(ellipse at 50% -15%,#1a3152 0,transparent 48%),#080d18;padding-bottom:88px}
header{position:sticky;top:0;z-index:20;background:rgba(8,13,24,.88);backdrop-filter:blur(18px);padding:15px clamp(14px,4vw,34px)}
h1{font-size:18px;letter-spacing:.08em}
main{max-width:1440px;padding-top:16px}
.metric,.panel{border-radius:18px;box-shadow:0 10px 30px rgba(0,0,0,.14)}
.metric{background:linear-gradient(145deg,rgba(22,36,58,.96),rgba(13,22,37,.96));padding:17px}
.metric strong{font-size:clamp(20px,2.4vw,29px)}
.section{padding:19px}
.section h2{font-size:12px;letter-spacing:.12em;color:#b9c9e2}
.refresh{border-radius:12px;padding:10px 14px;font-weight:700}
.app-nav{position:fixed;z-index:30;left:12px;right:12px;bottom:max(12px,env(safe-area-inset-bottom));display:grid;grid-template-columns:repeat(5,1fr);gap:4px;padding:8px;background:rgba(13,23,39,.94);border:1px solid #2b405e;border-radius:20px;box-shadow:0 14px 40px #0009;backdrop-filter:blur(22px)}
.app-nav button{appearance:none;border:0;background:transparent;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;min-height:48px;color:#91a4c0;text-decoration:none;font-size:10px;font-weight:700;letter-spacing:.02em}
.app-nav button span:first-child{font-size:17px;line-height:18px}
.app-nav button:active,.app-nav button:focus{color:#42e5aa}
.app-nav button.active{color:#42e5aa}
.app-view{display:none}.app-view.active{display:block}.app-nav button{color:#91a4c0;font-size:10px;font-weight:700;letter-spacing:.02em;min-height:48px;align-items:center;justify-content:center;flex-direction:column;gap:4px;border-radius:12px}.app-nav button.active{color:#42e5aa;background:#1c3142}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.split{grid-template-columns:1fr}.health{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:480px){header{padding:13px 14px}.sub{font-size:10px}.pill{font-size:9px;padding:6px 8px}.metric{padding:13px;border-radius:15px}.metric small{font-size:9px}.metric strong{font-size:20px;margin-top:7px}.section{padding:13px;margin-top:12px}.panel{border-radius:15px}.app-nav{left:8px;right:8px;bottom:max(8px,env(safe-area-inset-bottom));padding:6px}.app-nav a{font-size:9px}.actions{gap:6px}}

</style></head><body>
<header><div><h1>TROJAN V2 <span style="color:#6da9ff">/ COMMAND CENTER</span></h1><div class="sub">Solana AI paper scalper · system overview</div></div>
<div class="actions"><span class="pill">● PAPER MODE · LIVE LOCKED</span><button class="refresh" onclick="loadAll()">Refresh</button></div></header>
<main id="overview"><section class="app-view active" id="view-home"><div class="grid">
<div class="metric"><small>Paper balance</small><strong id="balance">—</strong></div><div class="metric"><small>Realized P/L</small><strong id="pnl">—</strong></div>
<div class="metric"><small>Win rate</small><strong id="winrate">—</strong></div><div class="metric"><small>Open positions</small><strong id="open">—</strong></div>
<div class="metric"><small>Daily realized P/L</small><strong id="daily">—</strong></div><div class="metric"><small>Daily loss used</small><strong id="loss">—</strong></div>
<div class="metric"><small>Closed trades</small><strong id="closed">—</strong></div><div class="metric"><small>Invested</small><strong id="invested">—</strong></div></div>
<div class="split"><section class="panel section" id="home-health"><h2>OPEN POSITIONS</h2><div style="overflow:auto"><table><thead><tr><th>Token</th><th>Strategy</th><th>Entry</th><th>Price</th><th>ROI</th><th>Size</th><th>DCA</th><th>Partial</th></tr></thead><tbody id="positions"></tbody></table></div></section>
<section class="panel section"><h2>SYSTEM HEALTH</h2><div class="health" id="health"></div><p class="sub" id="updated">Waiting for data…</p></section></div>
</section><section class="app-view" id="view-positions"><section class="panel section" id="positions-panel"><h2>OPEN POSITIONS</h2><div style="overflow:auto"><table><thead><tr><th>Token</th><th>Strategy</th><th>Entry</th><th>Price</th><th>ROI</th><th>Size</th><th>DCA</th><th>Partial</th></tr></thead><tbody id="positions-copy"></tbody></table></div></section></section><section class="app-view" id="view-trades"><section class="panel section" id="trades-panel"><h2>RECENT TRADES</h2><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Action</th><th>Token</th><th>Price</th><th>Amount</th><th>P/L</th><th>Strategy</th></tr></thead><tbody id="trades"></tbody></table></div></section>
</section><section class="app-view" id="view-analytics"><section class="panel section" id="analytics-panel"><h2>STRATEGY PERFORMANCE</h2><div class="health" id="performance"><div class="muted">Waiting for closed-trade data…</div></div></section>
<section class="panel section"><h2>FILTERING ANALYTICS</h2><div class="health" id="filter-summary"></div><div style="overflow:auto;margin-top:12px"><table><thead><tr><th>Time</th><th>Stage</th><th>Decision</th><th>Reason</th><th>Score</th><th>Token</th></tr></thead><tbody id="filter-events"></tbody></table></div></section>
</section><section class="app-view" id="view-system"><section class="panel section"><h2>STRATEGY RULE EDITOR</h2><p class="muted">JSON rule trees are saved to the bot database. Fields: liquidity_usd, volume_m5, volume_h24, price_change_m5, price_change_h1, price_change_h24, buy_ratio, score, source_count, txns_m5_total, pair_age_minutes, rsi_14, ema_9, ema_21, ema_cross_bullish, breakout_20, volume_spike_ratio. Operators: gt, gte, lt, lte, eq, between. Groups: all, any, not.</p><div class="split"><div><b>MAIN rules</b><textarea id="rules-main" rows="9" style="width:100%;margin-top:8px;background:#0b1423;color:#edf3ff;border:1px solid #24324a;border-radius:8px;padding:10px;font:12px monospace"></textarea><button class="refresh" onclick="saveRules('main')">Save MAIN</button></div><div><b>SNIPER rules</b><textarea id="rules-sniper" rows="9" style="width:100%;margin-top:8px;background:#0b1423;color:#edf3ff;border:1px solid #24324a;border-radius:8px;padding:10px;font:12px monospace"></textarea><button class="refresh" onclick="saveRules('sniper')">Save SNIPER</button></div></div><div class="muted" id="rules-status" style="margin-top:10px">Loading strategy configuration…</div></section>
<section class="panel section" id="system-panel"><h2>SYSTEM READINESS</h2><div class="health" id="readiness"></div><div class="muted" id="onchain-status" style="margin-top:10px"></div><div class="muted" id="indicator-status" style="margin-top:6px"></div></section>
<section class="panel section"><h2>COPY WALLET ACTIVITY</h2><div class="muted" style="margin-bottom:10px">Read-only wallet monitoring. No transactions are copied or signed.</div><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Wallet</th><th>Action</th><th>Mint</th><th>Token Δ</th><th>SOL Δ</th></tr></thead><tbody id="copy-trades"></tbody></table></div></section>
<section class="panel section"><h2>SYSTEM EVENTS</h2><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Event</th><th>Details</th><th>Token</th></tr></thead><tbody id="events"></tbody></table></div></section></section>
</main><nav class="app-nav" aria-label="Main navigation"><button type="button" class="active" data-view="home"><span>⌂</span><span>HOME</span></button><button type="button" data-view="positions"><span>◫</span><span>POSITIONS</span></button><button type="button" data-view="trades"><span>⇄</span><span>TRADES</span></button><button type="button" data-view="analytics"><span>◈</span><span>AI / STATS</span></button><button type="button" data-view="system"><span>⌁</span><span>SYSTEM</span></button></nav><footer>Paper trading controls · no wallet keys · live execution locked · Trojan V2</footer>
<script>
const esc=v=>String(v===null||v===undefined?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,n=2)=>v===null||v===undefined?'—':Number(v).toFixed(n);
const usd=v=>v===null||v===undefined?'—':'$'+fmt(v);
const short=v=>v?String(v).slice(0,6)+'…'+String(v).slice(-5):'—';
const time=v=>v?new Date(v).toLocaleTimeString():'—';
async function get(path){const r=await fetch(path);if(r.status===401){document.body.innerHTML='<main><h2>Authentication required</h2><p>Reload and sign in using your dashboard token.</p></main>';throw Error('auth');}if(!r.ok)throw Error('HTTP '+r.status);return r.json();}
if("serviceWorker" in navigator){window.addEventListener("load",()=>navigator.serviceWorker.register("/sw.js").catch(()=>{}));}
document.querySelectorAll(".app-nav button").forEach(button=>button.addEventListener("click",()=>{const target=button.dataset.view;document.querySelectorAll(".app-view").forEach(view=>view.classList.toggle("active",view.id==="view-"+target));document.querySelectorAll(".app-nav button").forEach(item=>item.classList.toggle("active",item===button));window.scrollTo({top:0,behavior:"instant"});}));
async function loadAll(){try{
const data=await Promise.all([get('/api/overview'),get('/api/trades?limit=35'),get('/api/events?limit=45'),get('/api/filtering'),get('/api/performance')]);
const o=data[0],t=data[1],e=data[2],f=data[3],p=data[4],a=o.account,s=o.stats;
document.getElementById('balance').textContent=usd(a.balance);
document.getElementById('pnl').textContent=usd(s.total_profit);
document.getElementById('pnl').className=s.total_profit>=0?'good':'bad';
document.getElementById('winrate').textContent=fmt(s.win_rate,1)+'%';
document.getElementById('open').textContent=o.position_count;
document.getElementById('daily').textContent=usd(a.daily_realized_pnl);
document.getElementById('loss').textContent=usd(a.daily_loss)+' / '+usd(o.risk.max_daily_loss);
document.getElementById('closed').textContent=s.total_trades;
document.getElementById('invested').textContent=usd(a.invested);
document.getElementById('positions').innerHTML=o.positions.length?o.positions.map(function(p){return '<tr><td class="token">'+p.token+'</td><td>'+p.strategy+'</td><td>'+fmt(p.entry_price,8)+'</td><td>'+fmt(p.current_price,8)+'</td><td class="'+(p.roi_percent>=0?'good':'bad')+'">'+(p.roi_percent===null?'—':fmt(p.roi_percent)+'%')+'</td><td>'+usd(p.invested)+'</td><td>'+p.dca_count+'</td><td>'+(p.partial_take_profit_done?'Done':'—')+'</td></tr>';}).join(''):'<tr><td colspan="8" class="muted">No open positions</td></tr>';
const m=o.market_data,r=o.runtime;
const checks=[['Market worker',m.worker_running?'Running':'Stopped'],['Pending market requests',m.pending_addresses],['Analyses queued',r.pending_analyses],['Discovery queue',r.discovery_pending],['Sniper queue',r.sniper_pending],['Live execution','LOCKED'],['Market cache',m.cache_entries],['Daily loss limit',usd(o.risk.max_daily_loss)]];
document.getElementById('health').innerHTML=checks.map(function(x){return '<div><b>'+x[0]+'</b><span class="muted">'+x[1]+'</span></div>';}).join('');
document.getElementById('updated').textContent='Updated '+new Date(o.timestamp).toLocaleString();
document.getElementById('trades').innerHTML=t.length?t.map(function(x){return '<tr><td>'+time(x.timestamp)+'</td><td class="'+(x.action==='SELL'?'bad':'good')+'">'+x.action+'</td><td class="token">'+short(x.token_address)+'</td><td>'+fmt(x.price,8)+'</td><td>'+usd(x.amount)+'</td><td class="'+(x.profit_loss>=0?'good':'bad')+'">'+usd(x.profit_loss)+'</td><td>'+x.strategy+'</td></tr>';}).join(''):'<tr><td colspan="7" class="muted">No trades recorded</td></tr>';
const decisions=f.summary.decisions||{};
document.getElementById('performance').innerHTML=p.strategies.length?p.strategies.map(function(x){return '<div><b>'+esc(x.strategy.toUpperCase())+'</b><span class="'+(x.realized_pnl>=0?'good':'bad')+'">'+usd(x.realized_pnl)+'</span><div class="muted">Closed: '+esc(x.closed_trades)+' · Wins: '+esc(x.wins)+' · Losses: '+esc(x.losses)+' · Win rate: '+fmt(x.win_rate,1)+'%</div></div>';}).join(''):'<div class="muted">No closed paper trades yet. Strategy stats appear after the first SELL.</div>';
document.getElementById('filter-summary').innerHTML=[['Recorded',f.summary.total_recorded],['Passed',decisions.PASS||0],['Rejected',decisions.REJECT||0],['Waiting',decisions.WAIT||0],['Errors',decisions.ERROR||0]].map(function(x){return '<div><b>'+esc(x[0])+'</b><span class="muted">'+esc(x[1])+'</span></div>';}).join('');
document.getElementById('filter-events').innerHTML=f.recent.length?f.recent.map(function(x){return '<tr><td>'+time(x.timestamp)+'</td><td>'+esc(x.stage)+'</td><td class="'+(x.decision==='PASS'?'good':x.decision==='REJECT'?'bad':'muted')+'">'+esc(x.decision)+'</td><td>'+esc((x.reason||'').slice(0,180))+'</td><td>'+fmt(x.score,1)+'</td><td class="token">'+esc(short(x.token_address))+'</td></tr>';}).join(''):'<tr><td colspan="6" class="muted">No filter decisions recorded yet</td></tr>';
document.getElementById('events').innerHTML=e.length?e.map(function(x){return '<tr><td>'+time(x.timestamp)+'</td><td>'+esc(x.event_type)+'</td><td>'+esc((x.message||'').slice(0,180))+'</td><td class="token">'+esc(short(x.token_address))+'</td></tr>';}).join(''):'<tr><td colspan="4" class="muted">No events recorded</td></tr>';
}catch(err){if(err.message!=='auth')document.getElementById('updated').textContent='Dashboard error: '+err.message;}}
async function loadAdvanced(){try{
const values=await Promise.all([get('/api/strategies'),get('/api/readiness'),get('/api/copy-trades?limit=50')]);
const s=values[0],h=values[1],c=values[2];
document.getElementById('rules-main').value=JSON.stringify(s.rules.main||{},null,2);
document.getElementById('rules-sniper').value=JSON.stringify(s.rules.sniper||{},null,2);
document.getElementById('rules-status').textContent=s.valid?'Rules valid · '+(s.enabled?'custom rules active':'no custom rules'):'Rule error: '+s.error;
const r=h.readiness;
document.getElementById('readiness').innerHTML=r.checks.map(x=>'<div><b>'+esc(x.name)+'</b><span class="'+(x.ok?'good':(x.required?'bad':'muted'))+'">'+(x.ok?'OK':'NOT CONFIGURED / CHECK')+'</span><div class="muted">'+esc(x.details||'')+'</div></div>').join('');
document.getElementById('onchain-status').textContent='On-chain RPC: '+(h.onchain.rpc_configured?'configured':'not configured')+' · protocols: '+h.onchain.supported_protocols.join(', ');
document.getElementById('indicator-status').textContent='OHLCV cache: '+h.indicators.ready_pools+' ready pools / '+h.indicators.cached_pools+' cached pools';
document.getElementById('onchain-status').textContent+=' · RPC health: '+(h.onchain.rpc_healthy?'healthy':'not healthy')+' · paper copy: '+(h.copy_monitor.paper_copy_enabled?'ON':'OFF');
document.getElementById('copy-trades').innerHTML=c.length?c.map(x=>'<tr><td>'+time(x.timestamp)+'</td><td class="token">'+esc(short(x.wallet))+'</td><td>'+esc(x.action)+'</td><td class="token">'+esc(short(x.token_mint))+'</td><td>'+fmt(x.token_delta,4)+'</td><td>'+fmt(x.sol_delta,4)+'</td></tr>').join(''):'<tr><td colspan="6" class="muted">No wallet activity yet</td></tr>';
}catch(err){if(err.message!=='auth')document.getElementById('rules-status').textContent='Advanced dashboard: '+err.message;}}
async function saveRules(strategy){try{const rules=JSON.parse(document.getElementById('rules-'+strategy).value);const response=await fetch('/api/strategies',{method:'POST',headers:{'Content-Type':'application/json','X-Trojan-Action':'1'},body:JSON.stringify({strategy:strategy,rules:rules})});const data=await response.json();if(!response.ok)throw Error(data.error||'Save failed');document.getElementById('rules-status').textContent=strategy.toUpperCase()+' rules saved. New candidate evaluations will use them.';loadAdvanced();}catch(err){document.getElementById('rules-status').textContent='Could not save: '+err.message;}}
loadAll();loadAdvanced();setInterval(loadAll,5000);setInterval(loadAdvanced,15000);
</script></body></html>"""


def start_dashboard():
    global _server, _server_thread

    if not os.getenv("TROJAN_DASHBOARD_TOKEN"):
        print(
            "[TROJAN V2] DASHBOARD DISABLED: set TROJAN_DASHBOARD_TOKEN",
            flush=True
        )
        return False

    with _server_lock:
        if _server_thread and _server_thread.is_alive():
            return True

        host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
        port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")))
        _server = make_server(host, port, app, threaded=True)
        _server_thread = threading.Thread(
            target=_server.serve_forever,
            name="trojan-dashboard",
            daemon=True
        )
        _server_thread.start()

    print(
        "[TROJAN V2] DASHBOARD STARTED | HOST: "
        + host + " | PORT: " + str(port)
        + " | AUTH: BASIC TOKEN | MODE: READ ONLY",
        flush=True
    )
    return True
