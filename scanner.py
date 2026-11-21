import requests

SOURCES = {
    "LATEST PROFILES":
        "https://api.dexscreener.com/token-profiles/latest/v1",

    "LATEST BOOSTS":
        "https://api.dexscreener.com/token-boosts/latest/v1",

    "TOP BOOSTS":
        "https://api.dexscreener.com/token-boosts/top/v1",

    "COMMUNITY TAKEOVERS":
        "https://api.dexscreener.com/community-takeovers/latest/v1"
}


def fetch_source(name, url):

    try:
        response = requests.get(
            url,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            print("INVALID SOURCE DATA:", name)
            return []

        print(name, "FOUND:", len(data))

        return data

    except (requests.RequestException, ValueError) as error:

        print("SOURCE ERROR:", name, error)
        return []


def scan_tokens():

    discovered = {}

    for source_name, url in SOURCES.items():

        tokens = fetch_source(source_name, url)

        for token in tokens:

            if token.get("chainId") != "solana":
                continue

            address = token.get("tokenAddress")

            if not address:
                continue

            if address not in discovered:

                discovered[address] = {
                    "tokenAddress": address,
                    "chainId": "solana",
                    "discovery_sources": []
                }

            if source_name not in discovered[address]["discovery_sources"]:

                discovered[address]["discovery_sources"].append(
                    source_name
                )

    results = list(discovered.values())

    results.sort(
        key=lambda token: len(token["discovery_sources"]),
        reverse=True
    )

    print("================================")
    print("MULTI-SOURCE SCANNER 4.0")
    print("TOTAL UNIQUE TOKENS:", len(results))
    print("================================")

    for token in results[:10]:

        print(
            "CANDIDATE:",
            token["tokenAddress"],
            "| SOURCES:",
            len(token["discovery_sources"]),
            "|",
            token["discovery_sources"]
        )

    return results


if __name__ == "__main__":

    scan_tokens()
