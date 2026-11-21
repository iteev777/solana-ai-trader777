import time
import requests
import paper_trading as paper

from scanner import scan_tokens
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

SCAN_INTERVAL = 30
MINIMUM_SCORE = 70
MAX_RUGCHECK_SCORE = 40


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

        # LIQUIDITY COLLAPSE CHECK

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

        if liquidity_status == "UNKNOWN":

            print("LIQUIDITY DATA UNKNOWN:", address)

        position = paper.positions[address]

        entry = position["entry_price"]
        invested = position["invested"]

        change = ((price / entry) - 1) * 100
        profit = invested * ((price / entry) - 1)

        print("--------------------------------")
        print("POSITION:", address)
        print("CHANGE:", round(change, 2), "%")
        print("PROFIT / LOSS:", round(profit, 3), "USD")

        result = paper.update_position(address, price)

        if isinstance(result, dict):

            record_sale(address, price, result)


def scan_and_trade():

    tokens = scan_tokens()

    print("CANDIDATES RECEIVED:", len(tokens))

    for token in tokens[:20]:

        address = token.get("tokenAddress")

        if not address:
            continue

        if address in paper.positions:
            continue

        market = get_market_data(address)

        if not market:
            continue

        market["discovery_sources"] = token.get(
            "discovery_sources", []
        )

        analysis = analyze_token(market)

        score = analysis["score"]

        print("--------------------------------")
        print("TOKEN:", address)
        print("DISCOVERY SOURCES:", analysis.get("source_count", 0))
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

        # RUGCHECK

        print("RUGCHECK CHECK:", address)

        report = get_rugcheck_report(address)

        if not isinstance(report, dict):
            print("TRADE BLOCKED: RugCheck report unavailable")
            continue

        rug_score = report.get("score_normalised")

        try:
            rug_score = float(rug_score)
        except (ValueError, TypeError):
            print("TRADE BLOCKED: Invalid RugCheck score")
            continue

        risks = report.get("risks", [])

        critical_risk = any(
            str(risk.get("level", "")).lower()
            in ["danger", "critical"]
            for risk in risks
            if isinstance(risk, dict)
        )

        print("RUGCHECK SCORE:", rug_score)

        if rug_score >= MAX_RUGCHECK_SCORE:
            print("TRADE BLOCKED: High RugCheck risk")
            continue

        if critical_risk:
            print("TRADE BLOCKED: Critical security warning")
            continue

        print("RUGCHECK PASSED")

        # RISK MANAGER

        allowed = can_open_trade(
            paper.balance,
            len(paper.positions),
            daily_loss,
            paper.TRADE_SIZE
        )

        if not allowed:
            print("TRADE BLOCKED BY RISK MANAGER")
            continue

        price = float(market.get("priceUsd") or 0)

        liquidity = float(
            market.get("liquidity", {}).get("usd") or 0
        )

        if price <= 0 or liquidity <= 0:
            continue

        result = paper.open_position(address, price)

        if isinstance(result, str) and result.startswith("PAPER BUY"):

            # Save initial liquidity baseline

            check_liquidity(address, liquidity)

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
            print("INITIAL LIQUIDITY:", liquidity, "USD")
            print("REMAINING BALANCE:", paper.balance, "USD")
            print("================================")

        time.sleep(0.5)


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

    initialize_database()

    print("================================")
    print("SOLANA MEMECOIN BOT 6.0")
    print("MODE: PAPER TRADING ONLY")
    print("STARTING BALANCE:", paper.STARTING_BALANCE, "USD")
    print("TRADE SIZE:", paper.TRADE_SIZE, "USD")
    print("TAKE PROFIT:", paper.TAKE_PROFIT * 100, "%")
    print("STOP LOSS:", paper.STOP_LOSS * 100, "%")
    print("SCAN INTERVAL:", SCAN_INTERVAL, "SECONDS")
    print("SECURITY: RUGCHECK")
    print("LIQUIDITY MONITOR: ACTIVE")
    print("EMERGENCY PAPER EXIT: ACTIVE")
    print("================================")

    while True:

        try:

            monitor_positions()

            scan_and_trade()

            print_statistics()

            print(
                "DAILY REALIZED P/L:",
                round(daily_realized_pnl, 2),
                "USD"
            )

            print(
                "DAILY LOSSES:",
                round(daily_loss, 2),
                "USD"
            )

            print("SCAN COMPLETED. WAITING...")
            print("--------------------------------")

        except Exception as error:

            print("BOT ERROR:", repr(error))

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":

    run_bot()
