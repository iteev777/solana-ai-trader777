# ==========================================
# TROJAN V2 - MAIN TRADING ENGINE
# BATCH MARKET DATA OPTIMIZATION
# ==========================================

import time
import queue
import math
from concurrent.futures import ThreadPoolExecutor

import paper_trading as paper
import sniper_scanner as sniper
import trading_manager as manager
import strategy as trading_strategy
import database

from scanner import scan_tokens
from discovery.token_aggregator import collect_tokens

from rugcheck import get_rugcheck_report
from liquidity_monitor import check_liquidity

from market_data import (
    get_market_data,
    get_market_data_batch,
    get_gecko_pool_data_batch
)
from indicators import fetch_pool_indicators
from solana_onchain import decode_pool_snapshot, get_onchain_status

from database import (
    initialize_database,
    save_analysis,
    get_trade_stats
)


# ==========================================
# CONFIGURATION
# ==========================================

WORKERS = 24
MAX_PENDING_TASKS = 500

# Rapid discovery and frequent paper-position checks.
SCAN_INTERVAL = 15
POSITION_CHECK_INTERVAL = 3
STATISTICS_INTERVAL = 30

MAX_RUGCHECK_SCORE = 40
SNIPER_MIN_LIQUIDITY = 2500

SNIPER_TOKEN_TIMEOUT = 300
DISCOVERY_TOKEN_TIMEOUT = 600

WAIT_RETRY_INTERVAL = 10
ERROR_RETRY_INTERVAL = 30
MAX_ANALYSIS_RETRIES = 2
REJECTED_TOKEN_RESCAN_INTERVAL = 60

executor = ThreadPoolExecutor(max_workers=WORKERS)
discovery_executor = ThreadPoolExecutor(max_workers=1)

discovery_future = None
pending_tasks = {}
copy_signal_queue = queue.Queue(maxsize=200)
seen_tokens = set()
rejected_tokens_until = {}

pending_sniper_tokens = {}
pending_discovery_tokens = {}

last_scan_time = 0
last_statistics_time = 0

last_prices = {}


# ==========================================
# HELPERS
# ==========================================

def log_event(event, address=None, reason=None):

    message = "[TROJAN V2] " + str(event)

    if address:
        message += " | TOKEN: " + str(address)

    if reason:
        message += " | REASON: " + str(reason)

    print(message, flush=True)


def safe_float(value):

    if isinstance(value, bool):
        return None

    try:
        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except (ValueError, TypeError, OverflowError):
        return None


def get_token_address(token):

    if not isinstance(token, dict):
        return None

    address = (
        token.get("tokenAddress")
        or token.get("address")
        or token.get("token_address")
        or token.get("mint")
    )

    if not isinstance(address, str):
        return None

    address = address.strip()

    return address if address else None


def get_liquidity(pair):

    if not isinstance(pair, dict):
        return None

    liquidity_data = pair.get("liquidity")

    if not isinstance(liquidity_data, dict):
        return None

    liquidity = safe_float(liquidity_data.get("usd"))

    if liquidity is None or liquidity < 0:
        return None

    return liquidity


def remove_pending_duplicates(address):

    pending_sniper_tokens.pop(address, None)
    pending_discovery_tokens.pop(address, None)


# ==========================================
# RETRY HANDLER
# ==========================================

def queue_retry(
    address,
    strategy_name,
    token,
    first_seen,
    attempts,
    delay,
    reason,
    retry_on_wait=False
):

    current_time = time.time()

    maximum_age = (
        SNIPER_TOKEN_TIMEOUT
        if strategy_name == "sniper"
        else DISCOVERY_TOKEN_TIMEOUT
    )

    token_age = current_time - first_seen

    # Missing market data is common immediately after a new launch.
    # Keep checking transient WAIT results until the token's own
    # discovery window expires instead of dropping it after two tries.
    if retry_on_wait:

        if token_age >= maximum_age:

            log_event(
                "TOKEN WAIT WINDOW EXPIRED",
                address,
                reason
            )

            seen_tokens.discard(address)
            remove_pending_duplicates(address)

            return

    elif attempts >= MAX_ANALYSIS_RETRIES:

        log_event(
            "ANALYSIS RETRIES EXHAUSTED",
            address,
            reason
        )

        seen_tokens.discard(address)
        remove_pending_duplicates(address)

        return

    item = {
        "first_seen": first_seen,
        "token": token,
        "attempts": attempts + 1,
        "retry_after": current_time + delay
    }

    if strategy_name == "sniper":

        pending_discovery_tokens.pop(address, None)
        pending_sniper_tokens[address] = item

    else:

        pending_sniper_tokens.pop(address, None)
        pending_discovery_tokens[address] = item

    seen_tokens.discard(address)

    log_event(
        "ANALYSIS RETRY SCHEDULED",
        address,
        str(reason) + " | ATTEMPT: " + str(attempts + 1)
    )


# ==========================================
# RUGCHECK
# ==========================================

def check_rugcheck(token_address):

    try:

        result = get_rugcheck_report(token_address)

        if not isinstance(result, dict):

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "EMPTY OR INVALID RESPONSE"
            )

            return False

        analysis = result.get("analysis")

        if not isinstance(analysis, dict):

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "ANALYSIS MISSING"
            )

            return False

        status = analysis.get("status")
        score = safe_float(analysis.get("score"))
        high_risks = safe_float(analysis.get("high_risks"))

        if status != "PASS":

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "STATUS: " + str(status)
            )

            return False

        if analysis.get("safe") is not True:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "TOKEN NOT SAFE"
            )

            return False

        if score is None or score >= MAX_RUGCHECK_SCORE:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "INVALID OR HIGH SCORE: " + str(score)
            )

            return False

        if high_risks is None or high_risks < 0:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "INVALID HIGH RISK COUNT"
            )

            return False

        if not high_risks.is_integer() or high_risks > 0:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "HIGH RISKS: " + str(high_risks)
            )

            return False

        return True

    except Exception as error:

        log_event(
            "RUGCHECK EXCEPTION",
            token_address,
            repr(error)
        )

        return False


# ==========================================
# TOKEN ANALYSIS
# ==========================================

def analyze_token_task(address, strategy_name, token):

    pair = token.get("market_data") if isinstance(token, dict) else None
    if not isinstance(pair, dict):
        pair = get_market_data(address)

    if not pair:

        return {
            "status": "waiting",
            "reason": "MARKET_DATA_MISSING",
            "address": address
        }

    try:

        pool_address = (
            token.get("poolAddress") or pair.get("pairAddress")
            or token.get("pool_address")
        )
        pool_protocol = (
            pair.get("dexId") or token.get("dexId")
            or token.get("poolName") or token.get("pool_name") or ""
        )
        onchain_snapshot = None
        if pool_address and get_onchain_status().get("rpc_configured"):
            try:
                onchain_snapshot = decode_pool_snapshot(
                    pool_address, pool_protocol, address
                )
            except Exception as error:
                from solana_onchain import record_onchain_error
                record_onchain_error(error)
                log_event("ONCHAIN POOL READ FAILED", address, repr(error))
        if isinstance(onchain_snapshot, dict):
            pair = dict(pair)
            pair.update(onchain_snapshot)

        indicators = fetch_pool_indicators(pool_address) if pool_address else {
            "ready": False, "reason": "Pool address unavailable"
        }

        price = safe_float(pair.get("priceUsd"))
        liquidity = get_liquidity(pair)

        if price is None or price <= 0:

            return {
                "status": "rejected",
                "reason": "INVALID_PRICE",
                "address": address
            }

        if liquidity is None:

            return {
                "status": "waiting",
                "reason": "LIQUIDITY_MISSING",
                "address": address
            }

        if strategy_name == "sniper":

            if liquidity < SNIPER_MIN_LIQUIDITY:

                return {
                    "status": "waiting",
                    "reason": "SNIPER_LIQUIDITY_TOO_LOW",
                    "address": address
                }

        token_data = dict(pair)
        token_data["address"] = address
        token_data["indicators"] = indicators
        token_data["onchain_verified"] = bool(
            isinstance(onchain_snapshot, dict)
            and onchain_snapshot.get("onchainVerified") is True
        )

        sources = token.get(
            "sources",
            token.get("discovery_sources", [strategy_name])
        )

        if not isinstance(sources, list):
            sources = [strategy_name]

        token_data["discovery_sources"] = [
            source.strip().lower()
            for source in sources
            if isinstance(source, str) and source.strip()
        ]

        if not token_data["discovery_sources"]:
            token_data["discovery_sources"] = [strategy_name]

        if not isinstance(token_data.get("liquidity"), dict):
            token_data["liquidity"] = {"usd": liquidity}

        account = paper.get_account_summary()

        open_positions = len(paper.get_positions())

        trade_decision = trading_strategy.evaluate_trade(
            token_data,
            account["balance"],
            open_positions,
            account["daily_loss"],
            strategy=strategy_name
        )

        if not isinstance(trade_decision, dict):

            return {
                "status": "rejected",
                "reason": "INVALID_STRATEGY_RESULT",
                "address": address
            }

        action = trade_decision.get("action")

        if action == "WAIT":
            return {
                "status": "waiting",
                "reason": trade_decision.get("reason", "WAITING_FOR_ENTRY"),
                "score": trade_decision.get("score", 0),
                "address": address
            }

        if action != "BUY_SIGNAL":
            return {
                "status": "rejected",
                "reason": trade_decision.get("reason", "STRATEGY_REJECTED"),
                "score": trade_decision.get("score", 0),
                "address": address
            }

        if not check_rugcheck(address):

            return {
                "status": "rejected",
                "reason": "RUGCHECK_FAILED",
                "address": address
            }

        score = safe_float(trade_decision.get("score"))

        if score is None or not 0 <= score <= 100:

            return {
                "status": "rejected",
                "reason": "INVALID_APPROVED_SCORE",
                "address": address
            }

        return {
            "status": "approved",
            "address": address,
            "price": price,
            "liquidity": liquidity,
            "score": score,
            "strategy": strategy_name,
            "analysis": trade_decision,
            "pool_address": pool_address,
            "market_source": pair.get("marketSource") or pair.get("dexId"),
            "pool_protocol": pool_protocol,
            "onchain_verified": bool(onchain_snapshot),
            "copy_wallet": token.get("copy_wallet") if isinstance(token, dict) else None
        }

    except Exception as error:

        log_event(
            "ANALYSIS EXCEPTION",
            address,
            repr(error)
        )

        return {
            "status": "error",
            "reason": "ANALYSIS_EXCEPTION",
            "address": address
        }


# ==========================================
# SCHEDULER
# ==========================================

def schedule_token(
    address,
    strategy_name,
    token=None,
    first_seen=None,
    attempts=0
):

    if not isinstance(address, str) or not address:
        return False

    if strategy_name not in ("main", "sniper"):
        return False

    if address in seen_tokens or address in pending_tasks:
        return False

    rejected_until = rejected_tokens_until.get(address, 0)

    if time.time() < rejected_until:
        return False

    if rejected_until:
        rejected_tokens_until.pop(address, None)

    if len(pending_tasks) >= MAX_PENDING_TASKS:

        log_event("TASK LIMIT REACHED", address)

        return False

    if address in paper.get_positions():
        return False

    if not isinstance(token, dict):
        token = {}

    seen_tokens.add(address)

    try:

        future = executor.submit(
            analyze_token_task,
            address,
            strategy_name,
            token
        )

        pending_tasks[address] = {
            "future": future,
            "strategy": strategy_name,
            "first_seen": (
                first_seen if first_seen is not None else time.time()
            ),
            "token": token,
            "attempts": attempts
        }

        log_event(
            "TOKEN QUEUED",
            address,
            strategy_name
        )

        return True

    except Exception as error:

        seen_tokens.discard(address)

        log_event(
            "TASK SUBMISSION ERROR",
            address,
            repr(error)
        )

        return False


# ==========================================
# COPY WALLET PAPER SIGNAL QUEUE
# ==========================================

def queue_copy_signal(event):
    if not isinstance(event, dict) or event.get("action") != "BUY":
        return False
    if not isinstance(event.get("token_mint"), str) or not event["token_mint"]:
        return False
    try:
        copy_signal_queue.put_nowait(dict(event))
        return True
    except queue.Full:
        log_event("COPY SIGNAL QUEUE FULL", event.get("token_mint"))
        return False


def process_copy_signals():
    processed = 0
    while processed < 25:
        try:
            event = copy_signal_queue.get_nowait()
        except queue.Empty:
            break
        processed += 1
        address = event.get("token_mint")
        token = {
            "tokenAddress": address,
            "chainId": "solana",
            "discovery_sources": ["copy_wallet"],
            "copy_wallet": event.get("wallet"),
            "copy_signature": event.get("signature")
        }
        queued = schedule_token(address, "sniper", token)
        log_event(
            "COPY BUY SIGNAL QUEUED" if queued else "COPY BUY SIGNAL SKIPPED",
            address,
            "wallet=" + str(event.get("wallet"))
        )


# ==========================================
# COMPLETED TASKS
# ==========================================

def process_completed_tasks():

    for address, task in list(pending_tasks.items()):

        future = task["future"]

        if not future.done():
            continue

        del pending_tasks[address]

        try:
            result = future.result()

        except Exception as error:

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                ERROR_RETRY_INTERVAL,
                "FUTURE_EXCEPTION: " + repr(error)
            )

            continue

        if not isinstance(result, dict):

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                ERROR_RETRY_INTERVAL,
                "INVALID_RESULT"
            )

            continue

        status = result.get("status")
        reason = result.get("reason", "NO_REASON")

        if status == "waiting":

            database_decision = getattr(__import__("database"), "record_filter_decision")
            database_decision(
                address, "MARKET_AND_STRATEGY", "WAIT", reason,
                score=result.get("score"),
                liquidity=result.get("liquidity"),
                source_count=len(task["token"].get("discovery_sources", []))
                if isinstance(task.get("token", {}).get("discovery_sources"), list) else 0
            )

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                WAIT_RETRY_INTERVAL,
                reason,
                retry_on_wait=True
            )

            continue

        if status == "error":

            database.record_filter_decision(
                address, "ANALYSIS", "ERROR", reason
            )

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                ERROR_RETRY_INTERVAL,
                reason
            )

            continue

        if status != "approved":

            __import__("database").record_filter_decision(
                address, "MARKET_AND_STRATEGY", "REJECT", reason,
                score=result.get("score"), liquidity=result.get("liquidity")
            )

            remove_pending_duplicates(address)

            log_event(
                "TOKEN REJECTED",
                address,
                reason
            )

            # Rejected candidates may become viable later as liquidity,
            # volume and buyer flow change. Revisit after a short cooldown
            # instead of blacklisting them for the entire process lifetime.
            seen_tokens.discard(address)
            rejected_tokens_until[address] = (
                time.time() + REJECTED_TOKEN_RESCAN_INTERVAL
            )

            continue

        score = safe_float(result.get("score"))
        price = safe_float(result.get("price"))
        strategy_name = result.get("strategy")
        analysis = result.get("analysis")

        if (
            score is None
            or price is None
            or price <= 0
            or strategy_name not in ("main", "sniper")
            or not isinstance(analysis, dict)
        ):

            log_event(
                "INVALID APPROVED RESULT",
                address
            )

            remove_pending_duplicates(address)

            continue

        __import__("database").record_filter_decision(
            address, "COMPLETE_PIPELINE", "PASS",
            "Approved for paper entry evaluation",
            score=score, liquidity=result.get("liquidity"),
            source_count=len(task["token"].get("discovery_sources", []))
            if isinstance(task.get("token", {}).get("discovery_sources"), list) else 0
        )

        try:

            save_analysis(
                address,
                score,
                analysis.get("action", "BUY_SIGNAL"),
                mode="paper"
            )

        except Exception as error:

            log_event(
                "ANALYSIS SAVE ERROR",
                address,
                repr(error)
            )

        try:

            trade_result = manager.process_approved_signal(
                address,
                price,
                strategy=strategy_name,
                metadata={
                    "pool_address": result.get("pool_address"),
                    "market_source": result.get("market_source"),
                    "pool_protocol": result.get("pool_protocol"),
                    "onchain_verified": result.get("onchain_verified"),
                    "copy_wallet": result.get("copy_wallet")
                }
            )

            log_event(
                "APPROVED SIGNAL",
                address,
                str(trade_result)
            )
            if isinstance(trade_result, dict) and str(trade_result.get("paper", "")).startswith("PAPER BUY:"):
                try:
                    from telegram_alerts import send_alert
                    send_alert(
                        "TROJAN PAPER BUY\nToken: " + address
                        + "\nStrategy: " + strategy_name
                        + "\nPrice: $" + str(price)
                        + "\nSize: $" + str(paper.TRADE_SIZE)
                        + "\nLive trading: LOCKED"
                    )
                except Exception as alert_error:
                    log_event("TELEGRAM ALERT ERROR", address, repr(alert_error))

        except Exception as error:

            log_event(
                "TRADE MANAGER ERROR",
                address,
                repr(error)
            )

        remove_pending_duplicates(address)


# ==========================================
# DISCOVERY
# ==========================================

def add_discovery_tokens():

    tokens = []

    try:
        result = scan_tokens()
        if isinstance(result, (list, tuple)):
            tokens.extend(result)
    except Exception as error:
        log_event("SCANNER ERROR", reason=repr(error))

    try:
        result = collect_tokens()
        if isinstance(result, (list, tuple)):
            tokens.extend(result)
    except Exception as error:
        log_event("AGGREGATOR ERROR", reason=repr(error))

    # Merge all feeds before scheduling so a DexScreener candidate does not
    # hide useful GeckoTerminal pool metadata for the same mint.
    merged = {}
    invalid = 0
    for token in tokens:
        if not isinstance(token, dict):
            invalid += 1
            continue
        address = get_token_address(token)
        if not address:
            invalid += 1
            continue
        candidate = merged.setdefault(address, {
            "tokenAddress": address,
            "chainId": "solana",
            "discovery_sources": []
        })
        for key, value in token.items():
            if key == "discovery_sources":
                sources = value if isinstance(value, list) else [value]
                for source in sources:
                    if isinstance(source, str) and source.strip():
                        normalized = source.strip().lower()
                        if normalized not in candidate["discovery_sources"]:
                            candidate["discovery_sources"].append(normalized)
            elif key == "market_data" and isinstance(value, dict):
                # Prefer a concrete pool snapshot over absent metadata.
                candidate[key] = value
            elif key not in candidate or candidate[key] in (None, "", {}, []):
                candidate[key] = value

    added = 0
    already_seen = 0
    already_pending = 0
    already_open = 0
    cooldown = 0
    blocked = 0

    for address, token in merged.items():
        if address in pending_tasks:
            already_pending += 1
            continue
        if address in seen_tokens:
            already_seen += 1
            continue
        if address in paper.get_positions():
            already_open += 1
            continue
        if time.time() < rejected_tokens_until.get(address, 0):
            cooldown += 1
            continue
        if schedule_token(address, "main", token):
            added += 1
        else:
            blocked += 1

    log_event(
        "DISCOVERY TOKENS QUEUED",
        reason=(
            "queued=" + str(added)
            + " | scanned=" + str(len(tokens))
            + " | unique=" + str(len(merged))
            + " | invalid=" + str(invalid)
            + " | already_seen=" + str(already_seen)
            + " | already_pending=" + str(already_pending)
            + " | open_positions=" + str(already_open)
            + " | reject_cooldown=" + str(cooldown)
            + " | blocked=" + str(blocked)
        )
    )


# ==========================================
# SNIPER QUEUE
# ==========================================

def receive_sniper_tokens():

    received = 0

    while True:

        try:
            token = sniper.token_queue.get_nowait()

        except queue.Empty:
            break

        except Exception as error:

            log_event("SNIPER QUEUE ERROR", reason=repr(error))
            break

        if not isinstance(token, dict):
            continue

        address = get_token_address(token)

        if not address:
            continue

        if address in seen_tokens or address in pending_tasks:
            continue

        pending_discovery_tokens.pop(address, None)

        pending_sniper_tokens[address] = {
            "token": token,
            "first_seen": time.time(),
            "attempts": 0,
            "retry_after": 0
        }

        received += 1

    if received:
        log_event("SNIPER TOKENS RECEIVED", reason=str(received))


def process_sniper_tokens():

    current_time = time.time()

    for address, item in list(pending_sniper_tokens.items()):

        if current_time - item["first_seen"] > SNIPER_TOKEN_TIMEOUT:

            log_event("SNIPER TOKEN EXPIRED", address)

            del pending_sniper_tokens[address]
            seen_tokens.add(address)

            continue

        if current_time < item.get("retry_after", 0):
            continue

        if address in pending_tasks:
            continue

        if address in paper.get_positions():

            del pending_sniper_tokens[address]
            continue

        schedule_token(
            address,
            "sniper",
            item.get("token", {}),
            first_seen=item["first_seen"],
            attempts=item.get("attempts", 0)
        )


# ==========================================
# DISCOVERY RETRIES
# ==========================================

def process_discovery_pending():

    current_time = time.time()

    for address, item in list(pending_discovery_tokens.items()):

        if current_time - item["first_seen"] > DISCOVERY_TOKEN_TIMEOUT:

            log_event("DISCOVERY TOKEN EXPIRED", address)

            del pending_discovery_tokens[address]
            seen_tokens.add(address)

            continue

        if current_time < item.get("retry_after", 0):
            continue

        if address in pending_tasks or address in pending_sniper_tokens:
            continue

        if address in paper.get_positions():

            del pending_discovery_tokens[address]
            continue

        schedule_token(
            address,
            "main",
            item.get("token", {}),
            first_seen=item["first_seen"],
            attempts=item.get("attempts", 0)
        )


# ==========================================
# SALE REPORTING
# ==========================================

def record_sale(address, result):

    if not isinstance(result, dict):
        return

    try:
        from telegram_alerts import send_alert
        send_alert(
            "TROJAN PAPER SELL\nToken: " + address
            + "\nReason: " + str(result.get("result", "SELL"))
            + "\nRealized P/L: $" + str(result.get("profit_loss", "n/a"))
        )
    except Exception as alert_error:
        log_event("TELEGRAM ALERT ERROR", address, repr(alert_error))

    log_event(
        "PAPER SALE",
        address,
        "P/L: "
        + str(result.get("profit_loss", "N/A"))
        + " | BALANCE: "
        + str(result.get("balance", "N/A"))
    )


# ==========================================
# POSITION MONITOR
# ==========================================

def monitor_positions():

    import service_health
    service_health.heartbeat("position_monitor")
    positions = list(paper.get_positions().items())

    if not positions:
        return

    addresses = [
        address
        for address, position in positions
        if isinstance(address, str) and address
    ]

    try:

        market_data = get_market_data_batch(addresses)

        if not isinstance(market_data, dict):
            log_event("POSITION BATCH ERROR", reason="INVALID RESPONSE")
            return

        missing_pool_addresses = [
            position.get("metadata", {}).get("pool_address")
            for address, position in positions
            if address not in market_data
            and isinstance(position, dict)
            and isinstance(position.get("metadata"), dict)
            and position["metadata"].get("pool_address")
        ]
        if missing_pool_addresses:
            gecko_fallback = get_gecko_pool_data_batch(missing_pool_addresses)
            market_data.update(gecko_fallback)

        # Prefer a fresh, owner-verified pool-account price when the stored
        # pool protocol is supported and an RPC endpoint is configured.
        if get_onchain_status().get("rpc_configured"):
            for address, position in positions:
                metadata = position.get("metadata", {}) if isinstance(position, dict) else {}
                pool_address = metadata.get("pool_address") if isinstance(metadata, dict) else None
                protocol = metadata.get("pool_protocol") if isinstance(metadata, dict) else None
                if not pool_address or not protocol:
                    continue
                try:
                    snapshot = decode_pool_snapshot(pool_address, protocol, address)
                    if snapshot:
                        existing = market_data.get(address, {})
                        merged = dict(existing) if isinstance(existing, dict) else {}
                        merged.update(snapshot)
                        market_data[address] = merged
                except Exception as error:
                    from solana_onchain import record_onchain_error
                    record_onchain_error(error)
                    log_event("ONCHAIN POSITION PRICE UNAVAILABLE", address, repr(error))

    except Exception as error:

        log_event(
            "POSITION BATCH ERROR",
            reason=repr(error)
        )

        return

    for address, position in positions:

        pair = market_data.get(address)

        if not pair:
            continue

        price = safe_float(pair.get("priceUsd"))

        if price is None or price <= 0:
            continue

        liquidity = get_liquidity(pair)

        last_prices[address] = price

        try:

            liquidity_status = check_liquidity(
                address,
                liquidity
            )

            if liquidity_status == "COLLAPSE":

                log_event(
                    "EMERGENCY LIQUIDITY EXIT",
                    address
                )

                result = paper.emergency_sell(
                    address,
                    price
                )

                record_sale(address, result)
                continue

            if liquidity_status in ("UNKNOWN", "WARNING"):

                log_event(
                    "LIQUIDITY STATUS",
                    address,
                    liquidity_status
                )

        except Exception as error:

            log_event(
                "LIQUIDITY MONITOR ERROR",
                address,
                repr(error)
            )

        try:

            result = paper.update_position(
                address,
                price
            )

            record_sale(address, result)

        except Exception as error:

            log_event(
                "POSITION UPDATE ERROR",
                address,
                repr(error)
            )


# ==========================================
# STATISTICS
# ==========================================

def print_statistics():

    try:

        stats = get_trade_stats("paper")
        account = paper.get_account_summary()

        print("\n========== TROJAN V2 ==========")

        print("PAPER BALANCE:", account["balance"])
        print("PAPER POSITIONS:", account["positions"])
        print("PAPER INVESTED:", account["invested"])

        print("MAIN POSITIONS:", paper.position_count("main"))
        print("SNIPER POSITIONS:", paper.position_count("sniper"))

        print("PENDING ANALYSES:", len(pending_tasks))
        print("DISCOVERY PENDING:", len(pending_discovery_tokens))
        print("SNIPER PENDING:", len(pending_sniper_tokens))

        print("CLOSED TRADES:", stats["total_trades"])
        print("WIN RATE:", stats["win_rate"])
        print("REALIZED P/L:", stats["total_profit"])

        print("LIVE MODE: SHADOW ONLY")
        print("LIVE EXECUTION: LOCKED")

        print("===============================\n")

    except Exception as error:

        log_event("STATISTICS ERROR", reason=repr(error))


# ==========================================
# MAIN LOOP
# ==========================================

def run_bot():

    global last_scan_time
    global last_statistics_time
    global discovery_future

    print("====================================")
    print("TROJAN V2 - SOLANA AI TRADING BOT")
    print("====================================")
    print("PAPER TRADING: ENABLED")
    print("WORKERS:", WORKERS)
    print("MAX PENDING TASKS:", MAX_PENDING_TASKS)
    print("MARKET DATA: BATCH ENABLED")
    print("LIVE EXECUTION: LOCKED")
    print("====================================")

    initialize_database()
    paper.refresh_account()

    try:
        from dashboard import start_dashboard
        start_dashboard()
    except Exception as dashboard_error:
        log_event("DASHBOARD START ERROR", reason=repr(dashboard_error))

    sniper.start_listener()

    try:
        from copy_trade import start_copy_monitor
        start_copy_monitor()
    except Exception as copy_error:
        log_event("COPY WATCH START ERROR", reason=repr(copy_error))

    log_event("SNIPER THREAD STARTED")
    log_event("BOT STARTED")

    while True:

        loop_start = time.time()

        try:

            # Free completed task slots first.

            process_completed_tasks()
            process_copy_signals()

            # Receive and schedule new tokens.

            receive_sniper_tokens()
            process_sniper_tokens()
            process_discovery_pending()

            # Run network-heavy discovery separately so API latency cannot
            # block the position monitor and stop-loss checks.
            if discovery_future is not None and discovery_future.done():

                try:
                    discovery_future.result()
                except Exception as error:
                    log_event("BACKGROUND DISCOVERY ERROR", reason=repr(error))

                discovery_future = None

            if (
                discovery_future is None
                and time.time() - last_scan_time >= SCAN_INTERVAL
            ):

                discovery_future = discovery_executor.submit(
                    add_discovery_tokens
                )

                last_scan_time = time.time()
                log_event("BACKGROUND DISCOVERY STARTED")

            # Monitor all positions through batch market data.

            monitor_positions()
            import service_health
            service_health.heartbeat("main_loop")

            if time.time() - last_statistics_time >= STATISTICS_INTERVAL:

                print_statistics()
                last_statistics_time = time.time()

        except Exception as error:

            log_event("MAIN LOOP ERROR", reason=repr(error))

        elapsed = time.time() - loop_start

        time.sleep(
            max(1, POSITION_CHECK_INTERVAL - elapsed)
        )


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    try:

        run_bot()

    except KeyboardInterrupt:

        log_event("BOT SHUTTING DOWN")

        executor.shutdown(
            wait=False,
            cancel_futures=True
        )

        discovery_executor.shutdown(
            wait=False,
            cancel_futures=True
        )
