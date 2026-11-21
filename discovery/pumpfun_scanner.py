import requests

API_URL = "https://frontend-api-v3.pump.fun/coins"

seen_tokens = set()


def scan_pumpfun():

    found_tokens = []

    try:
        response = requests.get(
            API_URL,
            params={
                "offset": 0,
                "limit": 50,
                "sort": "created_timestamp",
                "order": "DESC",
                "includeNsfw": "false"
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json"
            },
            timeout=15
        )

        response.raise_for_status()

        coins = response.json()

        if not isinstance(coins, list):
            return found_tokens

        for coin in coins:

            address = coin.get("mint")

            if not address or address in seen_tokens:
                continue

            seen_tokens.add(address)

            found_tokens.append({
                "tokenAddress": address,
                "name": coin.get("name", "Unknown"),
                "symbol": coin.get("symbol", "Unknown"),
                "discovery_sources": ["pumpfun"]
            })

        print("PUMPFUN NEW TOKENS:", len(found_tokens))

    except Exception as error:
        print("PUMPFUN SCANNER ERROR:", repr(error))

    return found_tokens
