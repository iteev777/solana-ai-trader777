import time
import requests
import paper_trading as paper
import sniper_scanner as sniper

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

POSITION_CHECK_INTERVAL = 5
SCAN_INTERVAL = 30

MINIMUM_SCORE = 70
MAX_RUGCHECK_SCORE = 40

SNIPER_MIN_LIQUIDITY = 2500
SNIPER_TOKEN_TIMEOUT = 120

DISCOVERY_TOKEN_TIMEOUT = 600

daily_loss = 0.0
daily_realized_pnl = 0.0

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

        if not isinstance(pairs, list) or not pairs:
            return None

        valid_pairs = [
            pair for pair in pairs
            if float(pair.get("priceUsd") or 0) > 0
        ]

        if not valid_pairs:
            return None

        return max(
            valid_pairs,
            key=lambda pair: float(
                pair.get("liquidity", {}).get("usd") or 0
            )
        )

    except (requests.RequestException, ValueError, TypeError) as error:

        print("MARKET DATA ERROR:", error)
        return None


def record_sale(address, price, result):

    global daily_loss
    global daily_realized_pnl

    if not isinstance(result, dict):
        return

    pnl = result["profit_loss"]

    daily_realized_pnl += pnl
    daily_loss += max(0, -pnl)

    save_trade(
        address,
        "SELL",
        price,
        paper.TRADE_SIZE + pnl,
        pnl
    )

    print("================================")
    print("PAPER SELL:", address)
    print("STRATEGY:", result.get("strategy", "main"))
    print("SELL REASON:", result["result"])
    print("PROFIT / LOSS:", pnl, "USD")
    print("NEW BALANCE:", result["balance"], "USD")
    print("================================")


def monitor_positions():

    for address in list(paper.positions.keys()):

        market = get_market_data(address)

        if not market:
            print("PRICE DATA UNAVAILABLE:", address)
            continue

        price = float(market.get("priceUsd") or 0)

        liquidity = float(
            market.get("liquidity", {}).get("usd") or 0
        )

        if price <= 0:
            continue

        liquidity_status = check_liquidity(
            address,
            liquidity
        )

        if liquidity_status == "COLLAPSE":

            print("EMERGENCY LIQUIDITY EXIT:", address)

            result = paper.emergency_sell(
                address,
                price
            )

            record_sale(address, price, result)
            continue

        position = paper.positions[address]

        entry = position["entry_price"]
        change = ((price / entry) - 1) * 100

        last_prices[address] = price

        print("--------------------------------")
        print("POSITION:", address)
        print("STRATEGY:", position.get("strategy", "main"))
        print("CHANGE:", round(change, 2), "%")
        print("LIQUIDITY STATUS:", liquidity_status)

        result = paper.update_position(address, price)

        if isinstance(result, dict):
            record_sale(address, price, result)


def check_rugcheck(address):

    print("RUGCHECK CHECK:", address)

    report = get_rugcheck_report(address)

    if not isinstance(report, dict):
        print("TRADE BLOCKED: RugCheck unavailable")
        return False

    try:
        rug_score = float(report.get("score_normalised"))
    except (ValueError, TypeError):
        print("TRADE BLOCKED: Invalid RugCheck score")
        return False

    risks = report.get("risks", [])

    critical_risk = any(
        str(risk.get("level", "")).lower()
        in ["danger", "critical"]
        for risk in risks
        if isinstance(risk, dict)
    )

    print("RUGCHECK SCORE:", rug_score)

    if rug_score >= MAX_RUGCHECK_SCORE or critical_risk:
        print("TRADE BLOCKED: RugCheck risk")
        return False

    print("RUGCHECK PASSED")
    return True


def get_all_discovery_tokens():

    all_tokens = []

    try:
        tokens = scan_tokens()

        for token in tokens:

            if not isinstance(token, dict):
                continue

            token["discovery_sources"] = (
                token.get("discovery_sources", ["main_scanner"])
            )

            all_tokens.append(token)

    except Exception as error:
        print("MAIN SCANNER ERROR:", repr(error))

    try:
        tokens = collect_tokens()

        for token in tokens:

            if isinstance(token, dict):
                all_tokens.append(token)

    except Exception as error:
        print("DISCOVERY ERROR:", repr(error))

    unique_tokens = {}

    for token in all_tokens:

        address = token.get("tokenAddress")

        if not address:
            continue

        if address not in unique_tokens:
            unique_tokens[address] = token

        else:

            existing = unique_tokens[address]

            sources = set(
                existing.get("discovery_sources", [])
            )

            sources.update(
                token.get("discovery_sources", [])
            )

            existing["discovery_sources"] = list(sources)

    result = list(unique_tokens.values())

    print("ALL DISCOVERY CANDIDATES:", len(result))

    return result


def scan_and_trade():

    tokens = get_all_discovery_tokens()

    print("CANDIDATES RECEIVED:", len(tokens))

    for token in tokens:

        address = token.get("tokenAddress")

        if not address:
            continue

        if address in paper.positions:
            continue

        if address in seen_discovery_tokens:
            continue

        if address in pending_discovery_tokens:
            continue

        pending_discovery_tokens[address] = {
            "token": token,
            "first_seen": time.time()
        }

        print("DISCOVERY TOKEN QUEUED:", address)

    for address in list(pending_discovery_tokens.keys()):

        pending = pending_discovery_tokens[address]

        age = time.time() - pending["first_seen"]

        if age > DISCOVERY_TOKEN_TIMEOUT:

            print("DISCOVERY TOKEN EXPIRED:", address)

            del pending_discovery_tokens[address]
            continue

        if address in paper.positions:

            del pending_discovery_tokens[address]
            continue

        market = get_market_data(address)

        if not market:

            print("DISCOVERY WAITING FOR PRICE:", address)
            continue

        price = float(market.get("priceUsd") or 0)

        liquidity = float(
            market.get("liquidity", {}).get("usd") or 0
        )

        if price <= 0 or liquidity <= 0:
            print("DISCOVERY WAITING FOR VALID MARKET:", address)
            continue

        token = pending["token"]

        market["discovery_sources"] = token.get(
            "discovery_sources", []
        )

        del pending_discovery_tokens[address]
        seen_discovery_tokens.add(address)

        analysis = analyze_token(market)

        score = analysis["score"]

        print("--------------------------------")
        print("TOKEN:", address)
        print("SOURCES:", market["discovery_sources"])
        print("PRICE:", price)
        print("LIQUIDITY:", liquidity)
        print("SCORE:", score)
        print("DECISION:", analysis["decision"])
        print("REASONS:", analysis["reasons"])

        save_analysis(
            address,
            score,
            analysis["decision"]
        )

        if score < MINIMUM_SCORE:
            continue

        if not check_rugcheck(address):
            continue

        allowed = can_open_trade(
            paper.balance,
            paper.position_count("main"),
            daily_loss,
            paper.TRADE_SIZE,
            strategy="main"
        )

        if not allowed:
            print("MAIN TRADE BLOCKED BY RISK MANAGER")
            continue

        result = paper.open_position(
            address,
            price,
            strategy="main"
        )

        if isinstance(result, str) and result.startswith("PAPER BUY"):

            last_prices[address] = price

            save_trade(
                address,
                "BUY",
                price,
                paper.TRADE_SIZE
            )

            print("================================")
            print("MAIN PAPER BUY:", address)
            print("ENTRY PRICE:", price)
            print("INVESTMENT:", paper.TRADE_SIZE, "USD")
            print("REMAINING BALANCE:", paper.balance, "USD")
            print("================================")

        time.sleep(0.5)


def receive_sniper_tokens():

    tokens = sniper.get_new_tokens()

    for token in tokens:

        address = token.get("address")

        if not address or address in paper.positions:
            continue

        if address in pending_sniper_tokens:
            continue

        pending_sniper_tokens[address] = {
            "token": token,
            "first_seen": time.time()
        }

        print("SNIPER TOKEN QUEUED:", address)


def process_sniper_tokens():

    receive_sniper_tokens()

    for address in list(pending_sniper_tokens.keys()):

        pending = pending_sniper_tokens[address]
        age = time.time() - pending["first_seen"]

        if age > SNIPER_TOKEN_TIMEOUT:
            print("SNIPER TOKEN EXPIRED:", address)
            del pending_sniper_tokens[address]
            continue

        if address in paper.positions:
            del pending_sniper_tokens[address]
            continue

        market = get_market_data(address)

        if not market:
            print("SNIPER WAITING FOR PRICE:", address)
            continue

        price = float(market.get("priceUsd") or 0)

        liquidity = float(
            market.get("liquidity", {}).get("usd") or 0
        )

        if price <= 0:
            continue

        if liquidity < SNIPER_MIN_LIQUIDITY:
            print("SNIPER WAITING FOR LIQUIDITY:", liquidity)
            continue

        allowed = can_open_trade(
            paper.balance,
            paper.position_count("sniper"),
            daily_loss,
            paper.TRADE_SIZE,
            strategy="sniper"
        )

        if not allowed:
            print("SNIPER TRADE BLOCKED BY RISK MANAGER")
            continue

        if not check_rugcheck(address):
            print("SNIPER TOKEN REJECTED")
            del pending_sniper_tokens[address]
            continue

        result = paper.open_position(
            address,
            price,
            strategy="sniper"
        )

        if isinstance(result, str) and result.startswith("PAPER BUY"):

            last_prices[address] = price

            save_trade(
                address,
                "BUY",
                price,
                paper.TRADE_SIZE
            )

            print("================================")
            print("SNIPER PAPER BUY:", address)
            print("ENTRY PRICE:", price)
            print("LIQUIDITY:", liquidity, "USD")
            print("INVESTMENT:", paper.TRADE_SIZE, "USD")
            print("REMAINING SHARED BALANCE:", paper.balance, "USD")
            print("================================")

            del pending_sniper_tokens[address]


def print_statistics():

    stats = get_trade_stats()

    total = stats["total_trades"]
    wins = stats["winning_trades"]

    win_rate = wins / total * 100 if total else 0

    current_profit = 0.0
    total_invested = 0.0

    main_positions = paper.position_count("main")
    sniper_positions = paper.position_count("sniper")

    for address, position in paper.positions.items():

        price = last_prices.get(address)

        if not price:
            continue

        invested = position["invested"]
        entry = position["entry_price"]

        current_profit += invested * ((price / entry) - 1)
        total_invested += invested

    print("========== BOT STATISTICS ==========")
    print("CLOSED TRADES:", total)
    print("WINNING TRADES:", wins)
    print("LOSING TRADES:", stats["losing_trades"])
    print("WIN RATE:", round(win_rate, 2), "%")
    print("REALIZED PROFIT:", stats["total_profit"], "USD")
    print("MAIN POSITIONS:", main_positions)
    print("SNIPER POSITIONS:", sniper_positions)
    print("PENDING SNIPER TOKENS:", len(pending_sniper_tokens))
    print("PENDING DISCOVERY TOKENS:", len(pending_discovery_tokens))
    print("OPEN INVESTMENT:", round(total_invested, 2), "USD")
    print("OPEN PROFIT / LOSS:", round(current_profit, 3), "USD")
    print("SHARED PAPER BALANCE:", round(paper.balance, 2), "USD")
    print("====================================")


def run_bot():

    global last_scan_time

    initialize_database()

    print("================================")
    print("SOLANA MEMECOIN BOT 9.1")
    print("MODE: PAPER TRADING ONLY")
    print("MAIN SCANNER: ACTIVE")
    print("DISCOVERY AGGREGATOR: ACTIVE")
    print("GECKOTERMINAL: ENABLED")
    print("PUMPFUN: ENABLED (API MAY BLOCK REQUESTS)")
    print("BIRDEYE: ENABLED IF API KEY EXISTS")
    print("LAUNCH SNIPER: STARTING")
    print("RUGCHECK: ACTIVE")
    print("LIQUIDITY MONITOR: ACTIVE")
    print("SHARED BALANCE:", paper.balance, "USD")
    print("================================")

    sniper.start_sniper_listener()

    while True:

        cycle_start = time.time()

        try:

            monitor_positions()
            process_sniper_tokens()

            if cycle_start - last_scan_time >= SCAN_INTERVAL:

                scan_and_trade()
                last_scan_time = time.time()

            print_statistics()

            print("DAILY REALIZED P/L:", round(daily_realized_pnl, 2), "USD")
            print("DAILY LOSSES:", round(daily_loss, 2), "USD")

        except Exception as error:

            print("BOT ERROR:", repr(error))

        elapsed = time.time() - cycle_start

        time.sleep(max(1, POSITION_CHECK_INTERVAL - elapsed))


if __name__ == "__main__":

    run_bot()
