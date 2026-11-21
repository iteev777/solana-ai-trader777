import time
import requests
import paper_trading as paper

from scanner import scan_tokens
from ai_analyzer import analyze_token
from database import initialize_database, save_analysis, save_trade
from risk_manager import can_open_trade


API_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"

SCAN_INTERVAL = 30
MINIMUM_SCORE = 70
daily_loss = 0.0


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

    total_profit = 0.0
    total_invested = 0.0

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
        profit = invested * (change / 100)

        print(
            "POSITION:", address,
            "| ENTRY:", entry,
            "| CURRENT:", price,
            "| PROFIT:", round(change, 2), "%"
        )

        total_profit += profit
        total_invested += invested

        result = paper.update_position(address, price)

        if isinstance(result, dict):

            pnl = result["profit_loss"]
            daily_loss += max(0, -pnl)

            save_trade(
                address,
                "SELL",
                price,
                paper.TRADE_SIZE + pnl,
                pnl
            )

            print("PAPER SELL:", address)
            print("SELL RESULT:", result)

    return total_profit, total_invested


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

            print("NEW PAPER POSITION:", address)
            print("ENTRY PRICE:", price)


def run_bot():

    initialize_database()

    print("================================")
    print("SOLANA PAPER TRADING BOT 2.1")
    print("MODE: PAPER TRADING ONLY")
    print("STARTING BALANCE: 100 USD")
    print("TRADE SIZE:", paper.TRADE_SIZE, "USD")
    print("SCAN INTERVAL:", SCAN_INTERVAL, "SECONDS")
    print("================================")

    while True:

        try:
            total_profit, total_invested = monitor_positions()

            scan_and_trade()

            profit_percent = (
                total_profit / total_invested * 100
                if total_invested > 0 else 0
            )

            print("--------------------------------")
            print("OPEN POSITIONS:", len(paper.positions))
            print("PAPER BALANCE:", round(paper.balance, 2), "USD")
            print("DAILY LOSSES:", round(daily_loss, 2), "USD")
            print(
                "CURRENT PROFIT:",
                round(total_profit, 2),
                "USD (",
                round(profit_percent, 2),
                "%)"
            )
            print("SCAN COMPLETED. WAITING...")
            print("--------------------------------")

        except Exception as error:
            print("BOT ERROR:", repr(error))

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_bot()
