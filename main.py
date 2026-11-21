import time
import requests
import paper_trading as paper

from scanner import scan_tokens
from ai_analyzer import analyze_token
from database import (
    initialize_database,
    save_analysis,
    save_trade,
    get_trade_stats
)
from risk_manager import can_open_trade


API_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"

SCAN_INTERVAL = 30
MINIMUM_SCORE = 70

daily_loss = 0.0
daily_realized_pnl = 0.0


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
            if float(pair.get("liquidity", {}).get("usd") or 0) > 0
            and float(pair.get("priceUsd") or 0) > 0
        ]

        if not valid_pairs:
            return None

        return max(
            valid_pairs,
            key=lambda pair: float(
                pair.get("liquidity", {}).get("usd") or 0
            )
        )

    except (requests.RequestException, ValueError) as error:
        print("MARKET DATA ERROR:", error)
        return None


def monitor_positions():

    global daily_loss
    global daily_realized_pnl

    for address in list(paper.positions.keys()):

        market = get_market_data(address)

        if not market:
            print("PRICE DATA UNAVAILABLE:", address)
            continue

        price = float(market.get("priceUsd") or 0)

        if price <= 0:
            continue

        position = paper.positions[address]

        entry = position["entry_price"]
        invested = position["invested"]

        change = ((price / entry) - 1) * 100
        profit = invested * (price / entry - 1)

        take_profit_distance = 30 - change
        stop_loss_distance = change + 20

        print("--------------------------------")
        print("TOKEN:", address)
        print("ENTRY PRICE:", entry)
        print("CURRENT PRICE:", price)
        print("CHANGE:", round(change, 2), "%")
        print("PROFIT / LOSS:", round(profit, 3), "USD")
        print("TAKE PROFIT DISTANCE:", round(take_profit_distance, 2), "percentage points")
        print("STOP LOSS DISTANCE:", round(stop_loss_distance, 2), "percentage points")

        if take_profit_distance <= 5 and change < 30:
            print("WARNING: APPROACHING TAKE PROFIT")

        if stop_loss_distance <= 5 and change > -20:
            print("WARNING: APPROACHING STOP LOSS")

        result = paper.update_position(address, price)

        if isinstance(result, dict):

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
            print("SELL REASON:", result["result"])
            print("PROFIT / LOSS:", pnl, "USD")
            print("NEW BALANCE:", result["balance"], "USD")
            print("================================")


def scan_and_trade():

    tokens = scan_tokens()

    for token in tokens[:10]:

        address = token.get("tokenAddress")

        if not address or address in paper.positions:
            continue

        market = get_market_data(address)

        if not market:
            continue

        price = float(market.get("priceUsd") or 0)

        if price <= 0:
            continue

        analysis = analyze_token(market)
        score = analysis["score"]

        save_analysis(
            address,
            score,
            analysis["decision"]
        )

        print(
            "TOKEN:", address,
            "| SCORE:", score,
            "| DECISION:", analysis["decision"]
        )

        if score < MINIMUM_SCORE:
            continue

        allowed = can_open_trade(
            paper.balance,
            len(paper.positions),
            daily_loss,
            paper.TRADE_SIZE
        )

        if not allowed:
            print("TRADE BLOCKED BY RISK MANAGER")
            continue

        result = paper.open_position(address, price)

        if isinstance(result, str) and result.startswith("PAPER BUY"):

            save_trade(
                address,
                "BUY",
                price,
                paper.TRADE_SIZE
            )

            print("================================")
            print("PAPER BUY:", address)
            print("ENTRY PRICE:", price)
            print("INVESTMENT:", paper.TRADE_SIZE, "USD")
            print("REMAINING BALANCE:", paper.balance, "USD")
            print("================================")


def print_statistics():

    stats = get_trade_stats()

    total = stats["total_trades"]
    wins = stats["winning_trades"]

    win_rate = (
        wins / total * 100
        if total > 0 else 0
    )

    current_profit = 0.0
    total_invested = 0.0

    for address, position in paper.positions.items():

        market = get_market_data(address)

        if not market:
            continue

        price = float(market.get("priceUsd") or 0)

        if price <= 0:
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
    print("OPEN POSITIONS:", len(paper.positions))
    print("OPEN INVESTMENT:", round(total_invested, 2), "USD")
    print("OPEN PROFIT / LOSS:", round(current_profit, 3), "USD")
    print("PAPER BALANCE:", round(paper.balance, 2), "USD")
    print("====================================")


def run_bot():

    global daily_realized_pnl

    initialize_database()

    print("================================")
    print("SOLANA PAPER TRADING BOT 3.1")
    print("MODE: PAPER TRADING ONLY")
    print("STARTING BALANCE:", paper.STARTING_BALANCE, "USD")
    print("TRADE SIZE:", paper.TRADE_SIZE, "USD")
    print("TAKE PROFIT:", paper.TAKE_PROFIT * 100, "%")
    print("STOP LOSS:", paper.STOP_LOSS * 100, "%")
    print("SCAN INTERVAL:", SCAN_INTERVAL, "SECONDS")
    print("================================")

    while True:

        try:
            monitor_positions()
            scan_and_trade()
            print_statistics()

            print("DAILY REALIZED P/L:", round(daily_realized_pnl, 2), "USD")
            print("DAILY LOSSES:", round(daily_loss, 2), "USD")
            print("SCAN COMPLETED. WAITING...")
            print("--------------------------------")

        except Exception as error:
            print("BOT ERROR:", repr(error))

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_bot()
