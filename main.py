import time
import requests

import paper_trading as paper
import sniper_scanner as sniper
import trading_manager as manager

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


API_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"

POSITION_CHECK_INTERVAL = 10
SCAN_INTERVAL = 60

MINIMUM_SCORE = 70
MAX_RUGCHECK_SCORE = 40

SNIPER_MIN_LIQUIDITY = 2500
SNIPER_TOKEN_TIMEOUT = 300

DISCOVERY_TOKEN_TIMEOUT = 600

last_scan_time = 0
last_prices = {}

pending_sniper_tokens = {}
pending_discovery_tokens = {}

seen_discovery_tokens = set()


def get_market_data(token_address):
    try:
        response = requests.get(
            API_URL + token_address,
            timeout=15
        )

        response.raise_for_status()
        pairs = response.json()

        if not pairs:
            return None

        valid_pairs = []

        for pair in pairs:
            liquidity = pair.get("liquidity", {}).get("usd", 0)
            price = pair.get("priceUsd")

            if not price:
                continue

            if float(price) <= 0:
                continue

            valid_pairs.append(pair)

        if not valid_pairs:
            return None

        return max(
            valid_pairs,
            key=lambda pair: float(
                pair.get("liquidity", {}).get("usd", 0) or 0
            )
        )

    except Exception as error:
        print("MARKET DATA ERROR:", error)
        return None


def get_token_price(token_address):
    pair = get_market_data(token_address)

    if not pair:
        return None

    try:
        return float(pair["priceUsd"])
    except (ValueError, TypeError, KeyError):
        return None


def record_sale(token_address, result):
    pnl = result["profit_loss"]

    save_trade(
        token_address,
        "SELL",
        result.get("returned_amount", 0),
        result.get("invested", 0),
        pnl,
        mode="paper",
        strategy=result.get("strategy", "main")
    )

    print(
        "PAPER SALE:",
        token_address,
        "| P/L:",
        pnl,
        "| BALANCE:",
        result["balance"]
    )


def monitor_positions():
    positions = list(paper.positions.items())

    for token_address, position in positions:
        price = get_token_price(token_address)

        if not price:
            continue

        last_prices[token_address] = price

        try:
            if check_liquidity(token_address, price) is False:
                result = paper.close_position(
                    token_address,
                    price,
                    "LIQUIDITY COLLAPSE"
                )

                if isinstance(result, dict):
                    record_sale(token_address, result)

                continue

        except Exception as error:
            print("LIQUIDITY MONITOR ERROR:", error)

        result = paper.update_position(
            token_address,
            price
        )

        if isinstance(result, dict):
            record_sale(token_address, result)


def check_rugcheck(token_address):
    try:
        report = get_rugcheck_report(token_address)

        if not report:
            print("RUGCHECK: NO REPORT", token_address)
            return False

        score = report.get("score_normalised")

        if score is None:
            print("RUGCHECK: SCORE MISSING", token_address)
            return False

        if float(score) >= MAX_RUGCHECK_SCORE:
            print("RUGCHECK: HIGH RISK", token_address)
            return False

        risks = report.get("risks", [])

        for risk in risks:
            level = str(risk.get("level", "")).lower()

            if level in ("danger", "critical"):
                print("RUGCHECK: CRITICAL RISK", token_address)
                return False

        return True

    except Exception as error:
        print("RUGCHECK ERROR:", error)
        return False


def get_all_discovery_tokens():
    all_tokens = []

    try:
        scanned = scan_tokens()

        if scanned:
            all_tokens.extend(scanned)

    except Exception as error:
        print("SCANNER ERROR:", error)

    try:
        discovered = collect_tokens()

        if discovered:
            all_tokens.extend(discovered)

    except Exception as error:
        print("AGGREGATOR ERROR:", error)

    merged = {}

    for token in all_tokens:
        if not isinstance(token, dict):
            continue

        address = (
            token.get("address")
            or token.get("token_address")
            or token.get("mint")
        )

        if not address:
            continue

        if address not in merged:
            merged[address] = token.copy()
        else:
            sources = merged[address].get("sources", [])
            new_sources = token.get("sources", [])

            if isinstance(sources, str):
                sources = [sources]

            if isinstance(new_sources, str):
                new_sources = [new_sources]

            merged[address]["sources"] = list(
                set(sources + new_sources)
            )

    return list(merged.values())


def add_discovery_tokens():
    tokens = get_all_discovery_tokens()
    now = time.time()

    added = 0

    for token in tokens:
        address = (
            token.get("address")
            or token.get("token_address")
            or token.get("mint")
        )

        if not address:
            continue

        if address in seen_discovery_tokens:
            continue

        seen_discovery_tokens.add(address)

        pending_discovery_tokens[address] = {
            "token": token,
            "first_seen": now
        }

        added += 1

    print("DISCOVERY TOKENS ADDED:", added)


def process_discovery_tokens():
    now = time.time()

    for address, item in list(pending_discovery_tokens.items()):
        if now - item["first_seen"] > DISCOVERY_TOKEN_TIMEOUT:
            print("DISCOVERY TOKEN EXPIRED:", address)
            del pending_discovery_tokens[address]
            continue

        if address in paper.positions:
            del pending_discovery_tokens[address]
            continue

        pair = get_market_data(address)

        if not pair:
            continue

        try:
            price = float(pair["priceUsd"])
            liquidity = float(
                pair.get("liquidity", {}).get("usd", 0) or 0
            )

            token_data = {
                "address": address,
                "price": price,
                "liquidity": liquidity,
                "volume": pair.get("volume", {}),
                "priceChange": pair.get("priceChange", {}),
                "pair": pair,
                "sources": item["token"].get("sources", [])
            }

            analysis = analyze_token(token_data)

            if not analysis:
                del pending_discovery_tokens[address]
                continue

            score = analysis.get("score", 0)

            save_analysis(
                address,
                score,
                str(analysis),
                mode="paper"
            )

            if score < MINIMUM_SCORE:
                print("DISCOVERY REJECTED:", address, score)
                del pending_discovery_tokens[address]
                continue

            if not check_rugcheck(address):
                del pending_discovery_tokens[address]
                continue

            if not can_open_trade(
                paper.balance,
                len(paper.positions),
                0
            ):
                print("RISK MANAGER BLOCKED:", address)
                continue

            result = manager.process_approved_signal(
                address,
                price,
                strategy="main"
            )

            print("APPROVED DISCOVERY SIGNAL:", address)
            print("TRADE RESULTS:", result)

            if result.get("paper") == "PAPER BUY: " + address:
                save_trade(
                    address,
                    "BUY",
                    price,
                    paper.TRADE_SIZE,
                    0,
                    mode="paper",
                    strategy="main"
                )

            del pending_discovery_tokens[address]

        except Exception as error:
            print("DISCOVERY PROCESS ERROR:", address, error)


def receive_sniper_tokens():
    try:
        while not sniper.token_queue.empty():
            token = sniper.token_queue.get_nowait()

            address = (
                token.get("mint")
                or token.get("address")
                or token.get("token_address")
            )

            if not address:
                continue

            if address in paper.positions:
                continue

            pending_sniper_tokens[address] = {
                "token": token,
                "first_seen": time.time()
            }

            print("SNIPER TOKEN RECEIVED:", address)

    except Exception as error:
        print("SNIPER RECEIVE ERROR:", error)


def process_sniper_tokens():
    now = time.time()

    for address, item in list(pending_sniper_tokens.items()):
        if now - item["first_seen"] > SNIPER_TOKEN_TIMEOUT:
            print("SNIPER TOKEN EXPIRED:", address)
            del pending_sniper_tokens[address]
            continue

        if address in paper.positions:
            del pending_sniper_tokens[address]
            continue

        pair = get_market_data(address)

        if not pair:
            print("SNIPER WAITING FOR MARKET DATA:", address)
            continue

        try:
            price = float(pair["priceUsd"])
            liquidity = float(
                pair.get("liquidity", {}).get("usd", 0) or 0
            )

            if liquidity < SNIPER_MIN_LIQUIDITY:
                print("SNIPER WAITING LIQUIDITY:", address, liquidity)
                continue

            token_data = {
                "address": address,
                "price": price,
                "liquidity": liquidity,
                "volume": pair.get("volume", {}),
                "priceChange": pair.get("priceChange", {}),
                "pair": pair,
                "sources": ["sniper"]
            }

            analysis = analyze_token(token_data)

            if not analysis:
                del pending_sniper_tokens[address]
                continue

            score = analysis.get("score", 0)

            save_analysis(
                address,
                score,
                str(analysis),
                mode="paper"
            )

            if score < MINIMUM_SCORE:
                print("SNIPER REJECTED:", address, score)
                del pending_sniper_tokens[address]
                continue

            if not check_rugcheck(address):
                del pending_sniper_tokens[address]
                continue

            if not can_open_trade(
                paper.balance,
                len(paper.positions),
                0
            ):
                print("SNIPER RISK BLOCKED:", address)
                continue

            result = manager.process_approved_signal(
                address,
                price,
                strategy="sniper"
            )

            print("SNIPER APPROVED:", address)
            print("SNIPER RESULTS:", result)

            if result.get("paper") == "PAPER BUY: " + address:
                save_trade(
                    address,
                    "BUY",
                    price,
                    paper.TRADE_SIZE,
                    0,
                    mode="paper",
                    strategy="sniper"
                )

            del pending_sniper_tokens[address]

        except Exception as error:
            print("SNIPER PROCESS ERROR:", address, error)


def print_statistics():
    try:
        stats = get_trade_stats("paper")

        print("\n========== BOT STATISTICS ==========")
        print("PAPER BALANCE:", round(paper.balance, 2))
        print("PAPER POSITIONS:", len(paper.positions))
        print(
            "PAPER INVESTED:",
            round(
                sum(
                    p["invested"]
                    for p in paper.positions.values()
                ),
                2
            )
        )
        print("LIVE MODE: SHADOW ONLY")
        print("LIVE EXECUTION: LOCKED")
        print("DISCOVERY PENDING:", len(pending_discovery_tokens))
        print("SNIPER PENDING:", len(pending_sniper_tokens))
        print("CLOSED PAPER TRADES:", stats.get("total_trades", 0))
        print("PAPER WIN RATE:", stats.get("win_rate", 0))
        print("PAPER REALIZED P/L:", stats.get("total_profit", 0))
        print("====================================\n")

    except Exception as error:
        print("STATISTICS ERROR:", error)


def run_bot():
    global last_scan_time

    print("====================================")
    print("SOLANA AI TRADING BOT")
    print("PAPER TRADING: ENABLED")
    print("LIVE SHADOW SIGNALS: ENABLED")
    print("LIVE EXECUTION: LOCKED")
    print("====================================")

    initialize_database()

    sniper.start_listener()

    while True:
        loop_start = time.time()

        monitor_positions()
        receive_sniper_tokens()
        process_sniper_tokens()
        process_discovery_tokens()

        if time.time() - last_scan_time >= SCAN_INTERVAL:
            add_discovery_tokens()
            last_scan_time = time.time()

        print_statistics()

        elapsed = time.time() - loop_start
        time.sleep(max(1, POSITION_CHECK_INTERVAL - elapsed))


if __name__ == "__main__":
    run_bot()
