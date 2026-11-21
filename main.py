import time
import requests

from scanner import scan_tokens
from ai_analyzer import analyze_token
from database import initialize_database, save_analysis

API_URL = "https://api.dexscreener.com/token-pairs/v1/solana/"

BALANCE = 100.00
SCAN_INTERVAL = 60


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

    initialize_database()

    print("================================")
    print("SOLANA AI TRADER 1.0")
    print("MODE: ANALYSIS ONLY")
    print("BALANCE:", BALANCE, "USD")
    print("================================")

    while True:

        try:
            tokens = scan_tokens()

            for token in tokens[:10]:

                address = token.get("tokenAddress")

                if not address:
                    continue

                market = get_market_data(address)

                if not market:
                    continue

                analysis = analyze_token(market)

                score = analysis["score"]
                decision = analysis["decision"]

                save_analysis(
                    address,
                    score,
                    decision
                )

                print(
                    "TOKEN:", address,
                    "| SCORE:", score,
                    "| DECISION:", decision
                )

            print("Scan completed. Waiting...")

        except Exception as error:
            print("BOT ERROR:", error)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_bot()
