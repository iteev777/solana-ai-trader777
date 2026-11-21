import time
import requests

import paper_trading as paper

from scanner import scan_tokens
from ai_analyzer import analyze_token
from database import initialize_database, save_analysis, save_trade
from risk_manager import can_open_trade


API_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"

BALANCE = 100.00
SCAN_INTERVAL = 30
MINIMUM_SCORE = 70

daily_loss = 0.00


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
            if pair.get("liquidity", {}).get("usd", 0)
        ]

        if not valid_pairs:
            return None

        return max(
            valid_pairs,
            key=lambda pair: pair.get(
                "liquidity", {}
            ).get("usd", 0)
        )

    except (requests.RequestException, ValueError):
        return None


def run_bot():
    global daily_loss

    initialize_database()

    print("================================")
    print("SOLANA PAPER TRADING BOT 1.0")
    print("MODE: PAPER TRADING ONLY")
    print("STARTING BALANCE:", BALANCE, "USD")
    print("TRADE SIZE:", paper.TRADE_SIZE, "USD")
    print("SCAN INTERVAL:", SCAN_INTERVAL, "SECONDS")
    print("================================")

    while True:
        try:

            # 1. Seurataan avoimia positioita
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

                change = ((price / entry) - 1) * 100

                print(
                    "POSITION:", address,
                    "| ENTRY:", entry,
                    "| CURRENT:", price,
                    "| PROFIT:", round(change, 2), "%"
                )

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
                    print("RESULT:", result)

            # 2. Etsitään uusia tokeneita
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

                # 3. Tarkistetaan ostosignaali
                if score < MINIMUM_SCORE:
                    continue

                allowed = can_open_trade(
                    paper.balance,
                    len(paper.positions),
                    daily_loss,
                    paper.TRADE_SIZE
                )

                if not allowed:
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
                    print("BALANCE:", round(paper.balance, 2), "USD")

            # 4. Tulostetaan yhteenveto
            print("--------------------------------")
            print("OPEN POSITIONS:", len(paper.positions))
            print("PAPER BALANCE:", round(paper.balance, 2), "USD")
            print("DAILY LOSSES:", round(daily_loss, 2), "USD")
            print("SCAN COMPLETED. WAITING...")
            print("--------------------------------")

        except Exception as error:
            print("BOT ERROR:", repr(error))

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_bot()
