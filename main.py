import time
from concurrent.futures import ThreadPoolExecutor

import paper_trading as paper
import sniper_scanner as sniper
import trading_manager as manager

from api_client import get_json
from scanner import scan_tokens
from discovery.token_aggregator import collect_tokens

from ai_analyzer import analyze_token
from rugcheck import get_rugcheck_report
from liquidity_monitor import check_liquidity

from database import (
    initialize_database,
    save_analysis,
    save_trade,
    get_trade_stats
)

from risk_manager import can_open_trade


# SETTINGS
API_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"

WORKERS = 4
SCAN_INTERVAL = 60
POSITION_CHECK_INTERVAL = 10

MINIMUM_SCORE = 70
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
last_prices = {}


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
            try:
                price = float(pair.get("priceUsd") or 0)
                liquidity = float(
                    pair.get("liquidity", {}).get("usd", 0) or 0
                )

                if price > 0:
                    valid_pairs.append((liquidity, pair))

            except (ValueError, TypeError):
                continue

        if not valid_pairs:
            return None

        return max(valid_pairs, key=lambda item: item[0])[1]

    except Exception as error:
        print("MARKET DATA ERROR:", repr(error))
        return None


def get_token_price(token_address):
    pair = get_market_data(token_address)

    if not pair:
        return None

    try:
        return float(pair["priceUsd"])
    except (ValueError, TypeError, KeyError):
        return None


def check_rugcheck(token_address):
    try:
        report = get_rugcheck_report(token_address)

        if not report:
            return False

        score = report.get("score_normalised")

        if score is None:
            return False

        if float(score) >= MAX_RUGCHECK_SCORE:
            return False

        for risk in report.get("risks", []):
            level = str(risk.get("level", "")).lower()

            if level in ("danger", "critical"):
                return False

        return True

    except Exception as error:
        print("RUGCHECK ERROR:", repr(error))
        return False


def analyze_token_task(address, strategy, token):
    """
    Worker task:
    Fetches market data, runs AI analysis and RugCheck.
    Does not open trades or modify the paper account.
    """

    pair = get_market_data(address)

    if not pair:
        return {
            "status": "waiting",
            "address": address,
            "strategy": strategy
        }

    try:
        price = float(pair.get("priceUsd") or 0)
        liquidity = float(
            pair.get("liquidity", {}).get("usd", 0) or 0
        )

        if price <= 0:
            return {"status": "rejected", "address": address}

        if strategy == "sniper" and liquidity < SNIPER_MIN_LIQUIDITY:
            return {
                "status": "waiting",
                "address": address,
                "strategy": strategy
            }

        token_data = {
            "address": address,
            "price": price,
            "liquidity": liquidity,
            "volume": pair.get("volume", {}),
            "priceChange": pair.get("priceChange", {}),
            "pair": pair,
            "sources": [strategy]
        }

        analysis = analyze_token(token_data)

        if not analysis:
            return {"status": "rejected", "address": address}

        score = analysis.get("score", 0)

        if score < MINIMUM_SCORE:
            return {
                "status": "rejected",
                "address": address,
                "score": score
            }

        if not check_rugcheck(address):
            return {
                "status": "rejected",
                "address": address,
                "reason": "RUGCHECK"
            }

        return {
            "status": "approved",
            "address": address,
            "price": price,
            "score": score,
            "strategy": strategy,
            "analysis": analysis
        }

    except Exception as error:
        print("ANALYSIS TASK ERROR:", address, repr(error))

        return {
            "status": "error",
            "address": address
        }


def schedule_token(address, strategy, token=None):
    if not address:
        return

    if address in paper.positions:
        return

    if address in pending_tasks:
        return

    if address in seen_tokens:
        return

    seen_tokens.add(address)

    future = executor.submit(
        analyze_token_task,
        address,
        strategy,
        token or {}
    )

    pending_tasks[address] = {
        "future": future,
        "strategy": strategy,
        "first_seen": time.time()
    }

    print("TOKEN QUEUED:", address, "|", strategy)


def process_completed_tasks():
    for address, task in list(pending_tasks.items()):
        future = task["future"]

        if not future.done():
            continue

        del pending_tasks[address]

        try:
            result = future.result()
        except Exception as error:
            print("TASK FAILED:", address, repr(error))
            continue

        status = result.get("status")

        if status == "waiting":
            # Token may become available later.
            seen_tokens.discard(address)

            if task["strategy"] == "sniper":
                pending_sniper_tokens[address] = {
                    "first_seen": task["first_seen"]
                }
            else:
                pending_discovery_tokens[address] = {
                    "first_seen": task["first_seen"]
                }

            continue

        if status != "approved":
            print(
                "TOKEN REJECTED:",
                address,
                "|",
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

        # Recheck risk immediately before opening.
        if not can_open_trade(
            paper.balance,
            len(paper.positions),
            paper.account["daily_loss"]
        ):
            print("RISK MANAGER BLOCKED:", address)
            continue

        trade_result = manager.process_approved_signal(
            address,
            result["price"],
            strategy=result["strategy"]
        )

        print("APPROVED SIGNAL:", address)
        print("SIGNAL RESULTS:", trade_result)

        if trade_result.get("paper") == "PAPER BUY: " + address:
            save_trade(
                address,
                "BUY",
                result["price"],
                paper.TRADE_SIZE,
                0,
                mode="paper",
                strategy=result["strategy"]
            )

        pending_sniper_tokens.pop(address, None)
        pending_discovery_tokens.pop(address, None)


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

        address = (
            token.get("address")
            or token.get("token_address")
            or token.get("mint")
        )

        if not address:
            continue

        if address in seen_tokens or address in pending_tasks:
            continue

        schedule_token(address, "main", token)
        added += 1

    print("DISCOVERY TOKENS QUEUED:", added)


def receive_sniper_tokens():
    received = 0

    try:
        while True:
            try:
                token = sniper.token_queue.get_nowait()
            except Exception:
                break

            address = (
                token.get("mint")
                or token.get("address")
                or token.get("token_address")
            )

            if not address:
                continue

            if address in seen_tokens or address in pending_tasks:
                continue

            pending_sniper_tokens[address] = {
                "token": token,
                "first_seen": time.time()
            }

            received += 1

    except Exception as error:
        print("SNIPER QUEUE ERROR:", repr(error))

    if received:
        print("SNIPER TOKENS RECEIVED:", received)


def process_sniper_tokens():
    now = time.time()

    for address, item in list(pending_sniper_tokens.items()):
        if now - item["first_seen"] > SNIPER_TOKEN_TIMEOUT:
            print("SNIPER TOKEN EXPIRED:", address)
            del pending_sniper_tokens[address]
            seen_tokens.add(address)
            continue

        if address in pending_tasks:
            continue

        if address in paper.positions:
            del pending_sniper_tokens[address]
            continue

        schedule_token(
            address,
            "sniper",
            item.get("token", {})
        )


def process_discovery_pending():
    now = time.time()

    for address, item in list(pending_discovery_tokens.items()):
        if now - item["first_seen"] > DISCOVERY_TOKEN_TIMEOUT:
            print("DISCOVERY TOKEN EXPIRED:", address)
            del pending_discovery_tokens[address]
            seen_tokens.add(address)
            continue

        if address in pending_tasks:
            continue

        if address in paper.positions:
            del pending_discovery_tokens[address]
            continue

        schedule_token(address, "main", {})


def record_sale(address, result):
    pnl = result["profit_loss"]

    save_trade(
        address,
        "SELL",
        result.get("returned_amount", 0),
        result.get("invested", 0),
        pnl,
        mode="paper",
        strategy=result.get("strategy", "main")
    )

    print(
        "PAPER SALE:",
        address,
        "| P/L:",
        pnl,
        "| BALANCE:",
        result["balance"]
    )


def monitor_positions():
    positions = list(paper.positions.items())

    for address, position in positions:
        price = get_token_price(address)

        if not price:
            continue

        last_prices[address] = price

        try:
            if check_liquidity(address, price) is False:
                result = paper.close_position(
                    address,
                    price,
                    "LIQUIDITY COLLAPSE"
                )

                if isinstance(result, dict):
                    record_sale(address, result)

                continue

        except Exception as error:
            print("LIQUIDITY MONITOR ERROR:", repr(error))

        result = paper.update_position(address, price)

        if isinstance(result, dict):
            record_sale(address, result)


def print_statistics():
    try:
        stats = get_trade_stats("paper")

        invested = sum(
            p["invested"]
            for p in paper.positions.values()
        )

        print("\n========== BOT STATISTICS ==========")
        print("PAPER BALANCE:", round(paper.balance, 2))
        print("PAPER POSITIONS:", len(paper.positions))
        print("PAPER INVESTED:", round(invested, 2))
        print("PENDING ANALYSES:", len(pending_tasks))
        print("DISCOVERY PENDING:", len(pending_discovery_tokens))
        print("SNIPER PENDING:", len(pending_sniper_tokens))
        print("CLOSED TRADES:", stats.get("total_trades", 0))
        print("WIN RATE:", stats.get("win_rate", 0))
        print("REALIZED P/L:", stats.get("total_profit", 0))
        print("LIVE MODE: SHADOW ONLY")
        print("LIVE EXECUTION: LOCKED")
        print("====================================\n")

    except Exception as error:
        print("STATISTICS ERROR:", repr(error))


def run_bot():
    global last_scan_time

    print("====================================")
    print("SOLANA AI TRADING BOT")
    print("PARALLEL WORKERS:", WORKERS)
    print("PAPER TRADING: ENABLED")
    print("LIVE SHADOW SIGNALS: ENABLED")
    print("LIVE EXECUTION: LOCKED")
    print("====================================")

    initialize_database()

    sniper.start_listener()

    while True:
        loop_start = time.time()

        receive_sniper_tokens()
        process_sniper_tokens()
        process_discovery_pending()

        process_completed_tasks()

        if time.time() - last_scan_time >= SCAN_INTERVAL:
            add_discovery_tokens()
            last_scan_time = time.time()

        monitor_positions()
        print_statistics()

        elapsed = time.time() - loop_start
        time.sleep(max(1, POSITION_CHECK_INTERVAL - elapsed))


if __name__ == "__main__":
    run_bot()
