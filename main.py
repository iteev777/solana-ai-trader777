# ==========================================
# TROJAN V2 - MAIN TRADING ENGINE
# ==========================================

import time
import queue
import math
from concurrent.futures import ThreadPoolExecutor

import paper_trading as paper
import sniper_scanner as sniper
import trading_manager as manager
import strategy as trading_strategy

from api_client import get_json
from scanner import scan_tokens
from discovery.token_aggregator import collect_tokens

from rugcheck import get_rugcheck_report
from liquidity_monitor import check_liquidity

from database import (
    initialize_database,
    save_analysis,
    get_trade_stats
)


# ==========================================
# TROJAN V2 CONFIGURATION
# ==========================================

API_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"

WORKERS = 4
MAX_PENDING_TASKS = 100

SCAN_INTERVAL = 60
POSITION_CHECK_INTERVAL = 10
STATISTICS_INTERVAL = 60

MAX_RUGCHECK_SCORE = 40
SNIPER_MIN_LIQUIDITY = 2500

SNIPER_TOKEN_TIMEOUT = 300
DISCOVERY_TOKEN_TIMEOUT = 600

executor = ThreadPoolExecutor(max_workers=WORKERS)

pending_tasks = {}
seen_tokens = set()

pending_sniper_tokens = {}
pending_discovery_tokens = {}

last_scan_time = 0
last_statistics_time = 0

last_prices = {}


# ==========================================
# DATA HELPERS
# ==========================================

def safe_float(value):

    try:
        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except (ValueError, TypeError):
        return None


def get_token_address(token):

    if not isinstance(token, dict):
        return None

    return (
        token.get("tokenAddress")
        or token.get("address")
        or token.get("token_address")
        or token.get("mint")
    )


def get_liquidity(pair):

    if not isinstance(pair, dict):
        return None

    liquidity_data = pair.get("liquidity")

    if not isinstance(liquidity_data, dict):
        return None

    return safe_float(liquidity_data.get("usd"))


# ==========================================
# MARKET DATA
# ==========================================

def get_market_data(token_address):

    try:

        pairs = get_json(
            API_URL + token_address,
            timeout=15,
            cache_ttl=10,
            max_retries=1
        )

        if not isinstance(pairs, list) or not pairs:
            return None

        valid_pairs = []

        for pair in pairs:

            if not isinstance(pair, dict):
                continue

            price = safe_float(pair.get("priceUsd"))

            if price is None or price <= 0:
                continue

            liquidity = get_liquidity(pair)

            liquidity_rank = (
                liquidity if liquidity is not None else -1
            )

            valid_pairs.append((liquidity_rank, pair))

        if not valid_pairs:
            return None

        return max(
            valid_pairs,
            key=lambda item: item[0]
        )[1]

    except Exception as error:

        print("TROJAN V2 MARKET DATA ERROR:", repr(error))

        return None


def get_token_price(token_address):

    pair = get_market_data(token_address)

    if not pair:
        return None

    return safe_float(pair.get("priceUsd"))


# ==========================================
# TROJAN V2 RUGCHECK
# ==========================================

def check_rugcheck(token_address):

    try:

        result = get_rugcheck_report(token_address)

        if not isinstance(result, dict):
            print("RUGCHECK UNKNOWN:", token_address)
            return False

        analysis = result.get("analysis")

        if not isinstance(analysis, dict):

            print("RUGCHECK INVALID ANALYSIS:", token_address)

            return False

        status = analysis.get("status")
        score = safe_float(analysis.get("score"))

        print(
            "RUGCHECK DECISION:",
            token_address,
            "| STATUS:",
            status,
            "| SCORE:",
            score
        )

        if status != "PASS":
            return False

        if analysis.get("safe") is not True:
            return False

        if score is None or score >= MAX_RUGCHECK_SCORE:
            return False

        if analysis.get("high_risks", 0) > 0:
            return False

        return True

    except Exception as error:

        print(
            "RUGCHECK ERROR:",
            token_address,
            repr(error)
        )

        return False


# ==========================================
# TOKEN ANALYSIS WORKER
# ==========================================

def analyze_token_task(address, strategy_name, token):

    pair = get_market_data(address)

    if not pair:

        return {
            "status": "waiting",
            "address": address,
            "strategy": strategy_name
        }

    try:

        price = safe_float(pair.get("priceUsd"))
        liquidity = get_liquidity(pair)

        if price is None or price <= 0:

            return {
                "status": "rejected",
                "address": address,
                "reason": "INVALID PRICE"
            }

        if liquidity is None:

            return {
                "status": "waiting",
                "address": address,
                "strategy": strategy_name
            }

        if strategy_name == "sniper":

            if liquidity < SNIPER_MIN_LIQUIDITY:

                return {
                    "status": "waiting",
                    "address": address,
                    "strategy": strategy_name
                }

        # Prepare market data for Trojan V2 strategy.
        token_data = dict(pair)

        token_data["address"] = address

        sources = token.get(
            "sources",
            token.get("discovery_sources", [strategy_name])
        )

        if not isinstance(sources, list):
            sources = [strategy_name]

        token_data["discovery_sources"] = sources

        if not isinstance(token_data.get("liquidity"), dict):
            token_data["liquidity"] = {"usd": liquidity}

        # Run AI analysis and risk evaluation through strategy.py.
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
                "address": address,
                "reason": "INVALID STRATEGY RESULT"
            }

        if trade_decision.get("action") != "BUY_SIGNAL":

            return {
                "status": "rejected",
                "address": address,
                "score": trade_decision.get("score", 0),
                "reason": trade_decision.get("reason", "STRATEGY REJECTED")
            }

        # Security check must pass before paper trading.
        if not check_rugcheck(address):

            return {
                "status": "rejected",
                "address": address,
                "score": trade_decision.get("score", 0),
                "reason": "RUGCHECK FAILED"
            }

        return {
            "status": "approved",
            "address": address,
            "price": price,
            "liquidity": liquidity,
            "score": trade_decision["score"],
            "strategy": strategy_name,
            "analysis": trade_decision
        }

    except Exception as error:

        print(
            "TROJAN V2 ANALYSIS ERROR:",
            address,
            repr(error)
        )

        return {
            "status": "error",
            "address": address
        }


# ==========================================
# TASK SCHEDULER
# ==========================================

def schedule_token(address, strategy_name, token=None):

    if not address:
        return False

    if address in seen_tokens:
        return False

    if address in pending_tasks:
        return False

    if len(pending_tasks) >= MAX_PENDING_TASKS:

        print("TASK LIMIT REACHED:", address)

        return False

    if address in paper.get_positions():
        return False

    seen_tokens.add(address)

    future = executor.submit(
        analyze_token_task,
        address,
        strategy_name,
        token or {}
    )

    pending_tasks[address] = {
        "future": future,
        "strategy": strategy_name,
        "first_seen": time.time(),
        "token": token or {}
    }

    print(
        "TOKEN QUEUED:",
        address,
        "| STRATEGY:",
        strategy_name
    )

    return True


# ==========================================
# COMPLETED ANALYSIS TASKS
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

            print(
                "TASK FAILED:",
                address,
                repr(error)
            )

            continue

        status = result.get("status")

        if status == "waiting":

            seen_tokens.discard(address)

            pending_item = {
                "first_seen": task["first_seen"],
                "token": task["token"]
            }

            if task["strategy"] == "sniper":
                pending_sniper_tokens[address] = pending_item
            else:
                pending_discovery_tokens[address] = pending_item

            continue

        if status != "approved":

            print(
                "TOKEN REJECTED:",
                address,
                "| REASON:",
                result.get("reason", status),
                "| SCORE:",
                result.get("score", "N/A")
            )

            continue

        save_analysis(
            address,
            result["score"],
            str(result["analysis"]),
            mode="paper"
        )

        trade_result = manager.process_approved_signal(
            address,
            result["price"],
            strategy=result["strategy"]
        )

        print("APPROVED SIGNAL:", address)
        print("SIGNAL RESULTS:", trade_result)

        pending_sniper_tokens.pop(address, None)
        pending_discovery_tokens.pop(address, None)


# ==========================================
# DISCOVERY SCANNERS
# ==========================================

def add_discovery_tokens():

    tokens = []

    try:

        result = scan_tokens()

        if result:
            tokens.extend(result)

    except Exception as error:

        print("SCANNER ERROR:", repr(error))

    try:

        result = collect_tokens()

        if result:
            tokens.extend(result)

    except Exception as error:

        print("AGGREGATOR ERROR:", repr(error))

    added = 0

    for token in tokens:

        if not isinstance(token, dict):
            continue

        address = get_token_address(token)

        if not address:
            continue

        if schedule_token(address, "main", token):
            added += 1

    print("DISCOVERY TOKENS QUEUED:", added)


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

            print("SNIPER QUEUE ERROR:", repr(error))

            break

        if not isinstance(token, dict):
            continue

        address = get_token_address(token)

        if not address:
            continue

        if address in seen_tokens:
            continue

        if address in pending_tasks:
            continue

        pending_sniper_tokens[address] = {
            "token": token,
            "first_seen": time.time()
        }

        received += 1

    if received:
        print("SNIPER TOKENS RECEIVED:", received)


def process_sniper_tokens():

    current_time = time.time()

    for address, item in list(pending_sniper_tokens.items()):

        if current_time - item["first_seen"] > SNIPER_TOKEN_TIMEOUT:

            print("SNIPER TOKEN EXPIRED:", address)

            del pending_sniper_tokens[address]
            seen_tokens.add(address)

            continue

        if address in pending_tasks:
            continue

        if address in paper.get_positions():

            del pending_sniper_tokens[address]

            continue

        schedule_token(
            address,
            "sniper",
            item.get("token", {})
        )


# ==========================================
# DISCOVERY RETRIES
# ==========================================

def process_discovery_pending():

    current_time = time.time()

    for address, item in list(pending_discovery_tokens.items()):

        if current_time - item["first_seen"] > DISCOVERY_TOKEN_TIMEOUT:

            print("DISCOVERY TOKEN EXPIRED:", address)

            del pending_discovery_tokens[address]
            seen_tokens.add(address)

            continue

        if address in pending_tasks:
            continue

        if address in paper.get_positions():

            del pending_discovery_tokens[address]

            continue

        schedule_token(
            address,
            "main",
            item.get("token", {})
        )


# ==========================================
# SALE REPORTING
# ==========================================

def record_sale(address, result):

    print(
        "PAPER SALE:",
        address,
        "| P/L:",
        result["profit_loss"],
        "| BALANCE:",
        result["balance"]
    )


# ==========================================
# POSITION MONITOR
# ==========================================

def monitor_positions():

    positions = list(
        paper.get_positions().items()
    )

    for address, position in positions:

        pair = get_market_data(address)

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

                print(
                    "EMERGENCY LIQUIDITY EXIT:",
                    address
                )

                result = paper.emergency_sell(
                    address,
                    price
                )

                if isinstance(result, dict):
                    record_sale(address, result)

                continue

            if liquidity_status in ("UNKNOWN", "WARNING"):

                print(
                    "LIQUIDITY STATUS:",
                    address,
                    "|",
                    liquidity_status
                )

        except Exception as error:

            print(
                "LIQUIDITY MONITOR ERROR:",
                address,
                repr(error)
            )

        result = paper.update_position(
            address,
            price
        )

        if isinstance(result, dict):
            record_sale(address, result)


# ==========================================
# TROJAN V2 STATISTICS
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

        print("STATISTICS ERROR:", repr(error))


# ==========================================
# TROJAN V2 MAIN LOOP
# ==========================================

def run_bot():

    global last_scan_time
    global last_statistics_time

    print("====================================")
    print("TROJAN V2 - SOLANA AI TRADING BOT")
    print("====================================")

    print("WORKERS:", WORKERS)
    print("MAX PENDING TASKS:", MAX_PENDING_TASKS)
    print("PAPER TRADING: ENABLED")
    print("MAIN STRATEGY: ENABLED")
    print("SNIPER STRATEGY: ENABLED")
    print("LIVE SHADOW SIGNALS: ENABLED")
    print("LIVE EXECUTION: LOCKED")

    print("====================================")

    initialize_database()

    paper.refresh_account()

    sniper.start_listener()

    print("SNIPER THREAD STARTED")
    print("TROJAN V2 STARTED")

    while True:

        loop_start = time.time()

        try:

            receive_sniper_tokens()

            process_sniper_tokens()

            process_discovery_pending()

            process_completed_tasks()

            if time.time() - last_scan_time >= SCAN_INTERVAL:

                add_discovery_tokens()

                last_scan_time = time.time()

            monitor_positions()

            if time.time() - last_statistics_time >= STATISTICS_INTERVAL:

                print_statistics()

                last_statistics_time = time.time()

        except Exception as error:

            print("MAIN LOOP ERROR:", repr(error))

        elapsed = time.time() - loop_start

        time.sleep(
            max(1, POSITION_CHECK_INTERVAL - elapsed)
        )


# ==========================================
# TROJAN V2 STARTUP
# ==========================================

if __name__ == "__main__":

    try:

        run_bot()

    except KeyboardInterrupt:

        print("TROJAN V2 SHUTTING DOWN")

        executor.shutdown(
            wait=False,
            cancel_futures=True
        )
