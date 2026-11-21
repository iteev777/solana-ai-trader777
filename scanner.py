import requests

API_URL = "https://api.dexscreener.com/token-boosts/latest/v1"


def scan_tokens():
    try:
        response = requests.get(
            API_URL,
            timeout=15
        )
        response.raise_for_status()

        tokens = response.json()

        if not isinstance(tokens, list):
            print("INVALID SCANNER RESPONSE")
            return []

        solana_tokens = []
        seen = set()

        for token in tokens:

            if token.get("chainId") != "solana":
                continue

            address = token.get("tokenAddress")

            if not address or address in seen:
                continue

            seen.add(address)
            solana_tokens.append(token)

        print(
            "FOUND SOLANA BOOSTED TOKENS:",
            len(solana_tokens)
        )

        return solana_tokens[:20]

    except (requests.RequestException, ValueError) as error:
        print("SCANNER ERROR:", error)
        return []


if __name__ == "__main__":
    scan_tokens()
