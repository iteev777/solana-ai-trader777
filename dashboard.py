import hmac
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

import database

app = Flask(__name__)
_server = None
_server_thread = None
_server_lock = threading.Lock()
_wallet_price_cache = {"checked_at": 0, "price_usd": None}
_wallet_portfolio_cache = {"checked_at": 0, "data": None}


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
    from market_data import get_market_data_batch, get_gecko_pool_data_batch

    open_positions = paper.get_positions()
    addresses = [
        address for address in open_positions
        if isinstance(address, str) and address
    ]

    # Dashboard price refresh is independent of the bot's in-memory monitor.
    # This avoids showing $0/partial after a restart or when last_prices has
    # not yet been populated for an already-open paper position.
    fresh_prices = {}
    if addresses:
        try:
            market_data = get_market_data_batch(addresses)
            if isinstance(market_data, dict):
                for address, pair in market_data.items():
                    if isinstance(pair, dict):
                        try:
                            price = float(pair.get("priceUsd") or 0)
                        except (TypeError, ValueError):
                            price = 0
                        if price > 0:
                            fresh_prices[address] = price

                missing_pool_addresses = [
                    (position.get("metadata") or {}).get("pool_address")
                    for address, position in open_positions.items()
                    if address not in fresh_prices
                    and isinstance(position, dict)
                    and isinstance(position.get("metadata"), dict)
                    and position["metadata"].get("pool_address")
                ]
                if missing_pool_addresses:
                    gecko_data = get_gecko_pool_data_batch(missing_pool_addresses)
                    if isinstance(gecko_data, dict):
                        for address, pair in gecko_data.items():
                            if isinstance(pair, dict):
                                try:
                                    price = float(pair.get("priceUsd") or 0)
                                except (TypeError, ValueError):
                                    price = 0
                                if price > 0:
                                    fresh_prices[address] = price
        except Exception as error:
            print("[DASHBOARD] Position price refresh failed:", repr(error), flush=True)

    result = []
    for address, position in open_positions.items():
        entry = float(position.get("entry_price") or 0)
        current = fresh_prices.get(address) or main.last_prices.get(address)
        current = float(current) if current else None
        roi = ((current / entry) - 1) * 100 if current and entry > 0 else None
        invested = float(position.get("invested") or 0)
        result.append({
            "token": address,
            "strategy": position.get("strategy", "main"),
            "entry_price": entry,
            "current_price": current,
            "invested": invested,
            "market_value": (
                invested * current / entry
                if current is not None and current > 0 and entry > 0 else None
            ),
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
        "positions_market_value": round(sum(
            float(position["market_value"])
            for position in positions if position.get("market_value") is not None
        ), 2),
        "positions_value_priced_count": sum(
            1 for position in positions if position.get("market_value") is not None
        ),
        "trade_rows": trade_count,
        "event_rows": event_count,
        "runtime": _runtime(),
        "market_data": get_market_data_status(),
        "api": get_api_status(),
    })


@app.get("/api/wallet-balance")
def api_wallet_balance():
    """Read-only SOL + SPL/Token-2022 portfolio snapshot for the configured wallet."""
    global _wallet_portfolio_cache
    wallet = os.getenv("TROJAN_PUBLIC_WALLET_ADDRESS", "").strip()
    if not wallet:
        return jsonify({"configured": False, "error": "Public wallet address is not configured"}), 503

    now = time.time()
    cached = _wallet_portfolio_cache.get("data")
    if cached and now - _wallet_portfolio_cache.get("checked_at", 0) < 15:
        return jsonify(cached)

    try:
        import math
        import solana_onchain
        import requests

        if len(solana_onchain._b58decode(wallet)) != 32:
            raise ValueError("Wallet address is not a valid Solana public key")

        balance_result = solana_onchain._rpc(
            "getBalance", [wallet, {"commitment": "confirmed"}]
        )
        lamports = int((balance_result or {}).get("value", 0))
        sol_balance = lamports / 1_000_000_000

        holdings = {}
        scan_errors = []
        for program_id in solana_onchain.TOKEN_PROGRAMS:
            try:
                response = solana_onchain._rpc(
                    "getTokenAccountsByOwner",
                    [
                        wallet,
                        {"programId": program_id},
                        {"encoding": "jsonParsed", "commitment": "confirmed"}
                    ]
                )
                for item in (response or {}).get("value", []):
                    parsed = (
                        (item.get("account") or {})
                        .get("data", {})
                        .get("parsed", {})
                        .get("info", {})
                    )
                    mint = parsed.get("mint")
                    token_amount = parsed.get("tokenAmount") or {}
                    raw_amount = token_amount.get("amount", "0")
                    decimals = int(token_amount.get("decimals", 0) or 0)
                    amount = float(raw_amount) / (10 ** decimals) if decimals >= 0 else 0
                    if not mint or not math.isfinite(amount) or amount <= 0:
                        continue
                    current = holdings.setdefault(mint, {
                        "mint": mint, "amount": 0.0, "decimals": decimals,
                        "program": program_id
                    })
                    current["amount"] += amount
            except Exception as error:
                scan_errors.append(str(error)[:160])

        assets = [{
            "mint": mint,
            "symbol": "SOL",
            "name": "Solana",
            "amount": sol_balance,
            "price_usd": None,
            "value_usd": None,
            "kind": "native"
        }]
        token_items = list(holdings.values())
        prices = {}
        stable_mints = {
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        }
        for item in token_items:
            if item["mint"] in stable_mints:
                prices[item["mint"]] = {"price": 1.0, "symbol": "USDC" if item["mint"].startswith("EPjF") else "USDT", "name": "USD stablecoin"}

        # DexScreener accepts up to 30 token mints per request. Fetch quotes in
        # bounded batches; illiquid or unsupported tokens remain explicitly unpriced.
        priceable = [item["mint"] for item in token_items if item["mint"] not in prices]
        for offset in range(0, min(len(priceable), 120), 30):
            batch = priceable[offset:offset + 30]
            try:
                quote = requests.get(
                    "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(batch),
                    timeout=4
                )
                quote.raise_for_status()
                pairs = quote.json() or []
                for pair in pairs if isinstance(pairs, list) else []:
                    if pair.get("chainId") != "solana":
                        continue
                    base = pair.get("baseToken") or {}
                    quote_token = pair.get("quoteToken") or {}
                    base_mint = base.get("address")
                    quote_mint = quote_token.get("address")
                    price_base = float(pair.get("priceUsd") or 0)
                    price_native = float(pair.get("priceNative") or 0)
                    liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
                    if not math.isfinite(price_base) or price_base <= 0:
                        continue
                    candidate = None
                    if base_mint in batch:
                        candidate = (base_mint, price_base, base)
                    elif quote_mint in batch and price_native > 0:
                        candidate = (quote_mint, price_base / price_native, quote_token)
                    if not candidate:
                        continue
                    mint, token_price, metadata = candidate
                    if not math.isfinite(token_price) or token_price <= 0:
                        continue
                    old = prices.get(mint)
                    if old is None or liquidity > old.get("liquidity", 0):
                        prices[mint] = {
                            "price": token_price,
                            "symbol": str(metadata.get("symbol") or "TOKEN")[:24],
                            "name": str(metadata.get("name") or "Unknown token")[:60],
                            "liquidity": liquidity
                        }
            except Exception as error:
                scan_errors.append("Price batch: " + str(error)[:130])

        sol_price = None
        try:
            sol_quote = requests.get(
                "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
                timeout=4
            )
            sol_quote.raise_for_status()
            sol_pairs = (sol_quote.json() or {}).get("pairs") or []
            valid_pairs = [
                pair for pair in sol_pairs
                if pair.get("chainId") == "solana" and pair.get("priceUsd")
                and float((pair.get("liquidity") or {}).get("usd") or 0) > 0
            ]
            if valid_pairs:
                best = max(valid_pairs, key=lambda pair: float((pair.get("liquidity") or {}).get("usd") or 0))
                sol_price = float(best["priceUsd"])
        except Exception as error:
            scan_errors.append("SOL quote: " + str(error)[:130])

        assets[0]["price_usd"] = sol_price
        assets[0]["value_usd"] = sol_balance * sol_price if sol_price is not None else None

        priced_total = assets[0]["value_usd"] or 0.0
        unpriced_count = 0
        for item in token_items:
            quote = prices.get(item["mint"])
            token_price = quote.get("price") if quote else None
            value = item["amount"] * token_price if token_price is not None else None
            if value is None:
                unpriced_count += 1
            else:
                priced_total += value
            assets.append({
                "mint": item["mint"],
                "symbol": quote.get("symbol", "UNPRICED") if quote else "UNPRICED",
                "name": quote.get("name", "No reliable market price") if quote else "No reliable market price",
                "amount": item["amount"],
                "price_usd": token_price,
                "value_usd": value,
                "kind": "spl"
            })

        assets[1:] = sorted(
            assets[1:],
            key=lambda asset: (asset["value_usd"] is not None, asset["value_usd"] or 0),
            reverse=True
        )
        result = {
            "configured": True,
            "wallet": wallet,
            "sol_balance": sol_balance,
            "sol_price_usd": sol_price,
            "sol_value_usd": assets[0]["value_usd"],
            "portfolio_value_usd": priced_total if sol_price is not None else None,
            "assets": assets,
            "token_count": len(token_items),
            "priced_token_count": len(token_items) - unpriced_count,
            "unpriced_token_count": unpriced_count,
            "token_scan_errors": scan_errors[:4],
            "price_lookup_limited": len(priceable) > 120,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "commitment": "confirmed",
            "asset_scope": "native SOL + SPL Token + Token-2022"
        }
        _wallet_portfolio_cache = {"checked_at": now, "data": result}
        return jsonify(result)
    except Exception as error:
        return jsonify({
            "configured": True,
            "wallet": wallet,
            "error": str(error)[:220],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }), 503


@app.get("/api/trades")
def api_trades():
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    return jsonify(_rows(
        """SELECT id, token_address, action, price, amount, profit_loss,
                  timestamp, strategy, copy_wallet
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
    import live_execution
    return jsonify({
        "readiness": service_health.get_readiness_report(),
        "services": service_health.get_service_status(),
        "onchain": get_onchain_status(),
        "indicators": get_indicator_status(),
        "copy_monitor": get_copy_monitor_status(),
        "jupiter": live_execution.get_execution_status(),
        "protocol_validation": database.get_protocol_validation_summary()
    })


@app.get("/api/copy-trades")
def api_copy_trades():
    return jsonify(database.get_recent_copy_trade_events(100))


@app.get("/api/copy-performance")
def api_copy_performance():
    return jsonify(database.get_copy_wallet_performance())


@app.get("/api/protocol-validation")
def api_protocol_validation():
    return jsonify(database.get_protocol_validation_summary())


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
<div class="actions"><span class="pill">● PAPER MODE · LIVE LOCKED</span><button class="refresh" onclick="loadHome();loadWallet()">Refresh</button></div></header>
<main id="overview"><section class="app-view active" id="view-home"><div class="grid">
<div class="metric"><small>Paper balance</small><strong id="balance">—</strong></div><div class="metric wallet-metric"><small>Phantom wallet · SOL</small><strong id="wallet-sol">—</strong><span class="sub" id="wallet-usd">Loading wallet…</span></div><div class="metric"><small>Realized P/L</small><strong id="pnl">—</strong></div>
<div class="metric"><small>Win rate</small><strong id="winrate">—</strong></div><div class="metric"><small>Open positions</small><strong id="open">—</strong></div>
<div class="metric"><small>Daily realized P/L</small><strong id="daily">—</strong></div><div class="metric"><small>Daily loss used</small><strong id="loss">—</strong></div>
<div class="metric"><small>Closed trades</small><strong id="closed">—</strong></div><div class="metric"><small>Open positions · live value</small><strong id="invested">—</strong></div></div>
<section class="panel section"><h2>PHANTOM WALLET ASSETS</h2><div class="muted" id="wallet-summary">Loading on-chain assets…</div><div style="overflow:auto;margin-top:10px"><table><thead><tr><th>Asset</th><th>Amount</th><th>Price</th><th>Value</th><th>Mint</th></tr></thead><tbody id="wallet-assets"><tr><td colspan="5" class="muted">Loading wallet…</td></tr></tbody></table></div></section>
<div class="split"><section class="panel section" id="home-health"><h2>OPEN POSITIONS</h2><div style="overflow:auto"><table><thead><tr><th>Token</th><th>Strategy</th><th>Entry</th><th>Price</th><th>ROI</th><th>Size</th><th>DCA</th><th>Partial</th></tr></thead><tbody id="positions"></tbody></table></div></section>
<section class="panel section"><h2>SYSTEM HEALTH</h2><div class="health" id="health"></div><p class="sub" id="updated">Waiting for data…</p></section></div>
</section><section class="app-view" id="view-positions"><section class="panel section" id="positions-panel"><h2>OPEN POSITIONS</h2><div style="overflow:auto"><table><thead><tr><th>Token</th><th>Strategy</th><th>Entry</th><th>Price</th><th>ROI</th><th>Size</th><th>DCA</th><th>Partial</th></tr></thead><tbody id="positions-copy"></tbody></table></div></section></section><section class="app-view" id="view-trades"><section class="panel section" id="trades-panel"><h2>RECENT TRADES</h2><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Action</th><th>Token</th><th>Price</th><th>Amount</th><th>P/L</th><th>Strategy</th></tr></thead><tbody id="trades"></tbody></table></div></section>
</section><section class="app-view" id="view-analytics"><section class="panel section" id="analytics-panel"><h2>STRATEGY PERFORMANCE</h2><div class="health" id="performance"><div class="muted">Waiting for closed-trade data…</div></div></section>
<section class="panel section"><h2>FILTERING ANALYTICS</h2><div class="health" id="filter-summary"></div><div style="overflow:auto;margin-top:12px"><table><thead><tr><th>Time</th><th>Stage</th><th>Decision</th><th>Reason</th><th>Score</th><th>Token</th></tr></thead><tbody id="filter-events"></tbody></table></div></section>
</section><section class="app-view" id="view-system"><section class="panel section"><h2>STRATEGY RULE EDITOR</h2><p class="muted">JSON rule trees are saved to the bot database. Fields: liquidity_usd, volume_m5, volume_h24, price_change_m5, price_change_h1, price_change_h24, buy_ratio, score, source_count, txns_m5_total, pair_age_minutes, rsi_14, ema_9, ema_21, ema_cross_bullish, breakout_20, volume_spike_ratio, timeframe_ready_count, bullish_timeframes, bearish_timeframes, macd_histogram, macd_bullish, bollinger_percent_b, atr_percent, stochastic_k, above_vwap. Operators: gt, gte, lt, lte, eq, between. Groups: all, any, not.</p><div class="split"><div><b>MAIN rules</b><textarea id="rules-main" rows="9" style="width:100%;margin-top:8px;background:#0b1423;color:#edf3ff;border:1px solid #24324a;border-radius:8px;padding:10px;font:12px monospace"></textarea><button class="refresh" onclick="saveRules('main')">Save MAIN</button></div><div><b>SNIPER rules</b><textarea id="rules-sniper" rows="9" style="width:100%;margin-top:8px;background:#0b1423;color:#edf3ff;border:1px solid #24324a;border-radius:8px;padding:10px;font:12px monospace"></textarea><button class="refresh" onclick="saveRules('sniper')">Save SNIPER</button></div></div><div class="muted" id="rules-status" style="margin-top:10px">Loading strategy configuration…</div></section>
<section class="panel section" id="system-panel"><h2>SYSTEM READINESS</h2><div class="health" id="readiness"></div><div class="muted" id="onchain-status" style="margin-top:10px"></div><div class="muted" id="indicator-status" style="margin-top:6px"></div></section>
<section class="panel section"><h2>COPY WALLET PAPER PERFORMANCE</h2><div class="muted" style="margin-bottom:10px">Wallet-attributed realized paper P/L, win rate, open exposure and observed activity.</div><div class="health" id="copy-performance"><div class="muted">Loading wallet performance…</div></div></section>
<section class="panel section"><h2>11-PROTOCOL MAINNET VALIDATION</h2><div class="muted" style="margin-bottom:10px">Counts only confirmed on-chain pool decodes observed by this running bot; zero means not yet verified.</div><div class="health" id="protocol-validation"><div class="muted">Loading protocol validation…</div></div></section>
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
async function loadWallet(){
 try{
  const w=await get('/api/wallet-balance');
  if(!w.configured){document.getElementById('wallet-total').textContent='Not set';document.getElementById('wallet-usd').textContent='Set TROJAN_PUBLIC_WALLET_ADDRESS';return;}
  if(w.error)throw Error(w.error);
  document.getElementById('wallet-total').textContent=w.portfolio_value_usd===null?'—':usd(w.portfolio_value_usd);
  document.getElementById('wallet-usd').textContent=fmt(w.sol_balance,4)+' SOL · '+w.token_count+' SPL assets · '+w.unpriced_token_count+' unpriced · '+short(w.wallet);
  document.getElementById('wallet-summary').textContent='Confirmed on-chain snapshot · '+w.priced_token_count+'/'+w.token_count+' SPL tokens priced · refreshed '+time(w.timestamp)+(w.price_lookup_limited?' · price lookup capped at 120 tokens':'');
  const rows=(w.assets||[]).map(function(a){
   const name=esc(a.symbol||'TOKEN');
   const amount=fmt(a.amount, a.kind==='native'?4:6);
   const price=a.price_usd===null?'Unpriced':usd(a.price_usd);
   const value=a.value_usd===null?'—':usd(a.value_usd);
   return '<tr><td><b>'+name+'</b><div class="muted">'+esc(a.name||'')+'</div></td><td>'+amount+'</td><td>'+price+'</td><td>'+(a.value_usd===null?'<span class="muted">Unpriced</span>':value)+'</td><td class="token">'+esc(short(a.mint))+'</td></tr>';
  });
  document.getElementById('wallet-assets').innerHTML=rows.length?rows.join(''):'<tr><td colspan="5" class="muted">No SPL assets found</td></tr>';
 }catch(err){document.getElementById('wallet-usd').textContent='Wallet update failed: '+err.message;document.getElementById('wallet-summary').textContent='Wallet data unavailable';}
}
async function loadHome(){
 try{
  const o=await get('/api/overview'),a=o.account,s=o.stats;
  document.getElementById('balance').textContent=usd(a.balance);
  document.getElementById('pnl').textContent=usd(s.total_profit);
  document.getElementById('pnl').className=s.total_profit>=0?'good':'bad';
  document.getElementById('winrate').textContent=fmt(s.win_rate,1)+'%';
  document.getElementById('open').textContent=o.position_count;
  document.getElementById('daily').textContent=usd(a.daily_realized_pnl);
  document.getElementById('loss').textContent=usd(a.daily_loss)+' / '+usd(o.risk.max_daily_loss);
  document.getElementById('closed').textContent=s.total_trades;
  document.getElementById('invested').textContent=usd(o.positions_market_value)+(o.position_count>o.positions_value_priced_count?' · partial':'');
  const positionRows=o.positions.length?o.positions.map(function(p){return '<tr><td class="token">'+esc(short(p.token))+'</td><td>'+esc(p.strategy)+'</td><td>'+fmt(p.entry_price,8)+'</td><td>'+fmt(p.current_price,8)+'</td><td class="'+(p.roi_percent>=0?'good':'bad')+'">'+(p.roi_percent===null?'—':fmt(p.roi_percent)+'%')+'</td><td>'+usd(p.invested)+'</td><td>'+p.dca_count+'</td><td>'+(p.partial_take_profit_done?'Done':'—')+'</td></tr>';}).join(''):'<tr><td colspan="8" class="muted">No open positions</td></tr>';
  document.getElementById('positions').innerHTML=positionRows;
  document.getElementById('positions-copy').innerHTML=positionRows;
  const m=o.market_data,r=o.runtime;
  const checks=[['Market worker',m.worker_running?'Running':'Stopped'],['Pending market requests',m.pending_addresses],['Analyses queued',r.pending_analyses],['Discovery queue',r.discovery_pending],['Sniper queue',r.sniper_pending],['Live execution','LOCKED'],['Market cache',m.cache_entries],['Daily loss limit',usd(o.risk.max_daily_loss)]];
  document.getElementById('health').innerHTML=checks.map(function(x){return '<div><b>'+x[0]+'</b><span class="muted">'+x[1]+'</span></div>';}).join('');
  document.getElementById('updated').textContent='Updated '+new Date(o.timestamp).toLocaleString();
 }catch(err){if(err.message!=='auth')document.getElementById('updated').textContent='Dashboard error: '+err.message;}
}
async function loadTrades(){
 try{const t=await get('/api/trades?limit=50');
 document.getElementById('trades').innerHTML=t.length?t.map(function(x){return '<tr><td>'+time(x.timestamp)+'</td><td class="'+(x.action==='SELL'?'bad':'good')+'">'+esc(x.action)+'</td><td class="token">'+esc(short(x.token_address))+'</td><td>'+fmt(x.price,8)+'</td><td>'+usd(x.amount)+'</td><td class="'+(x.profit_loss>=0?'good':'bad')+'">'+usd(x.profit_loss)+'</td><td>'+esc(x.strategy)+'</td></tr>';}).join(''):'<tr><td colspan="7" class="muted">No trades recorded</td></tr>';
 }catch(err){document.getElementById('trades').innerHTML='<tr><td colspan="7" class="muted">'+esc(err.message)+'</td></tr>';}
}
async function loadAnalytics(){
 try{
  const values=await Promise.all([get('/api/filtering'),get('/api/performance')]),f=values[0],p=values[1];
  const decisions=f.summary.decisions||{};
  document.getElementById('performance').innerHTML=p.strategies.length?p.strategies.map(function(x){return '<div><b>'+esc(x.strategy.toUpperCase())+'</b><span class="'+(x.realized_pnl>=0?'good':'bad')+'">'+usd(x.realized_pnl)+'</span><div class="muted">Closed: '+esc(x.closed_trades)+' · Wins: '+esc(x.wins)+' · Losses: '+esc(x.losses)+' · Win rate: '+fmt(x.win_rate,1)+'%</div></div>';}).join(''):'<div class="muted">No closed paper trades yet. Strategy stats appear after the first SELL.</div>';
  document.getElementById('filter-summary').innerHTML=[['Recorded',f.summary.total_recorded],['Passed',decisions.PASS||0],['Rejected',decisions.REJECT||0],['Waiting',decisions.WAIT||0],['Errors',decisions.ERROR||0]].map(function(x){return '<div><b>'+esc(x[0])+'</b><span class="muted">'+esc(x[1])+'</span></div>';}).join('');
  document.getElementById('filter-events').innerHTML=f.recent.length?f.recent.map(function(x){return '<tr><td>'+time(x.timestamp)+'</td><td>'+esc(x.stage)+'</td><td class="'+(x.decision==='PASS'?'good':x.decision==='REJECT'?'bad':'muted')+'">'+esc(x.decision)+'</td><td>'+esc((x.reason||'').slice(0,180))+'</td><td>'+fmt(x.score,1)+'</td><td class="token">'+esc(short(x.token_address))+'</td></tr>';}).join(''):'<tr><td colspan="6" class="muted">No filter decisions recorded yet</td></tr>';
 }catch(err){document.getElementById('performance').textContent='Analytics unavailable: '+err.message;}
}
async function loadAdvanced(){
 try{
  const values=await Promise.all([
   get('/api/strategies'),get('/api/readiness'),get('/api/copy-trades'),
   get('/api/copy-performance'),get('/api/protocol-validation')
  ]);
  const s=values[0],h=values[1],c=values[2],cp=values[3],pv=values[4];
  document.getElementById('rules-main').value=JSON.stringify(s.rules.main||{},null,2);
  document.getElementById('rules-sniper').value=JSON.stringify(s.rules.sniper||{},null,2);
  document.getElementById('rules-status').textContent=s.valid?'Rules valid · '+(s.enabled?'custom rules active':'no custom rules'):'Rule error: '+s.error;
  const r=h.readiness;
  document.getElementById('readiness').innerHTML=r.checks.map(x=>'<div><b>'+esc(x.name)+'</b><span class="'+(x.ok?'good':(x.required?'bad':'muted'))+'">'+(x.ok?'OK':'NOT CONFIGURED / CHECK')+'</span><div class="muted">'+esc(x.details||'')+'</div></div>').join('');
  document.getElementById('onchain-status').textContent='On-chain RPC: '+(h.onchain.rpc_configured?'configured':'not configured')+' · protocols: '+h.onchain.supported_protocols.join(', ');
  document.getElementById('indicator-status').textContent='OHLCV cache: '+h.indicators.ready_pools+' ready pools / '+h.indicators.cached_pools+' cached pools';
  const j=h.jupiter||{};
  document.getElementById('indicator-status').textContent+=' · Jupiter: '+(j.quote_provider||'unavailable')+' · API key: '+(j.api_key_configured?'configured':'keyless / rate-limited')+' · max impact: '+(j.max_price_impact_percent??'—')+'% · live execution: LOCKED';
  document.getElementById('indicator-status').textContent+=' · unsigned simulation: '+(j.simulation_available?'AVAILABLE':'not configured')+' · signing/broadcast: DISABLED';
  const stream=h.onchain.price_stream||{};
  document.getElementById('onchain-status').textContent+=' · RPC health: '+(h.onchain.rpc_healthy?'healthy':'not healthy')+' · price stream: '+(stream.running?'LIVE':'OFF')+' ('+(stream.subscriptions||0)+' pools, '+(stream.notifications||0)+' updates, '+(stream.last_latency_ms==null?'n/a':stream.last_latency_ms+' ms')+') · paper copy: '+(h.copy_monitor.paper_copy_enabled?'ON':'OFF');
  document.getElementById('copy-trades').innerHTML=c.length?c.map(x=>'<tr><td>'+time(x.timestamp)+'</td><td class="token">'+esc(short(x.wallet))+'</td><td>'+esc(x.action)+'</td><td class="token">'+esc(short(x.token_mint))+'</td><td>'+fmt(x.token_delta,4)+'</td><td>'+fmt(x.sol_delta,4)+'</td></tr>').join(''):'<tr><td colspan="6" class="muted">No wallet activity yet</td></tr>';
  document.getElementById('copy-performance').innerHTML=cp.length?cp.map(x=>'<div><b class="token">'+esc(short(x.wallet))+'</b><span class="'+(x.realized_pnl>=0?'good':'bad')+'">'+usd(x.realized_pnl)+'</span><div class="muted">Win rate '+fmt(x.win_rate,1)+'% · closed '+x.closed_trades+' · daily loss '+usd(x.daily_loss)+' · open '+x.open_positions+' · exposure '+usd(x.open_exposure_usd)+' · observed '+x.observed_events+'</div></div>').join(''):'<div class="muted">No copy-wallet performance recorded yet</div>';
  document.getElementById('protocol-validation').innerHTML=(pv.protocols||[]).map(x=>'<div><b>'+esc(x.protocol)+'</b><span class="'+(x.successes>0?'good':'muted')+'">'+x.successes+' / '+x.attempts+' verified</span><div class="muted">Failures '+x.failures+' · success '+fmt(x.success_rate,1)+'%'+(x.last_error?' · '+esc(x.last_error):'')+'</div></div>').join('');
 }catch(err){if(err.message!=='auth')document.getElementById('rules-status').textContent='Advanced dashboard: '+err.message;}
}
document.querySelectorAll(".app-nav button").forEach(button=>button.addEventListener("click",()=>{
 const target=button.dataset.view;
 document.querySelectorAll(".app-view").forEach(view=>view.classList.toggle("active",view.id==="view-"+target));
 document.querySelectorAll(".app-nav button").forEach(item=>item.classList.toggle("active",item===button));
 window.scrollTo(0,0);
 if(target==="positions"||target==="home")loadHome();
 if(target==="trades")loadTrades();
 if(target==="analytics")loadAnalytics();
 if(target==="system")loadAdvanced();
}));
async function saveRules(strategy){try{const rules=JSON.parse(document.getElementById('rules-'+strategy).value);const response=await fetch('/api/strategies',{method:'POST',headers:{'Content-Type':'application/json','X-Trojan-Action':'1'},body:JSON.stringify({strategy:strategy,rules:rules})});const data=await response.json();if(!response.ok)throw Error(data.error||'Save failed');document.getElementById('rules-status').textContent=strategy.toUpperCase()+' rules saved. New candidate evaluations will use them.';loadAdvanced();}catch(err){document.getElementById('rules-status').textContent='Could not save: '+err.message;}}
loadHome();loadWallet();setInterval(loadHome,5000);setInterval(loadWallet,15000);
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
