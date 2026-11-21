import requests

API_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

def scan_tokens():
    try:
        response = requests.get(API_URL, timeout=15)
        response.raise_for_status()

        tokens = response.json()
        solana_tokens = [
            token for token in tokens
            if token.get("chainId") == "solana"
        ]

        print(f"Found {len(solana_tokens)} Solana tokens")

        for token in solana_tokens[:10]:
            print(
                "Token:",
                token.get("tokenAddress"),
                "| URL:",
                token.get("url")
            )

        return solana_tokens

    except requests.RequestException as error:
        print("Scanner error:", error)
        return []

if __name__ == "__main__":
    scan_tokens()
