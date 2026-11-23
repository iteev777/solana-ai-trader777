"""Independent DexScreener discovery feeds, matching ScreenerBot's source split."""
from api_client import get_json

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
LATEST_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
TOP_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"

# Retained for callers that still need the legacy combined result.
ENDPOINTS = [
    ("dexscreener_profiles", PROFILES_URL),
    ("dexscreener_latest_boosts", LATEST_BOOSTS_URL),
    ("dexscreener_top_boosts", TOP_BOOSTS_URL),
]


def _scan_feed(source, url, *, chain_filter=False):
    """Fetch and normalize one feed; failures stay local to this feed."""
    found = []
    try:
        kwargs = {
            "timeout": 10,
            "cache_ttl": 20,
            "max_retries": 1,
        }
        if chain_filter:
            kwargs["params"] = {"chainId": "solana"}
        payload = get_json(url, **kwargs)
        if not isinstance(payload, list):
            print("DEXSCREENER INVALID RESPONSE:", source, flush=True)
            return found

        seen = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("chainId", "")).strip().lower() != "solana":
                continue
            address = item.get("tokenAddress") or item.get("address")
            if not isinstance(address, str) or not address.strip():
                continue
            address = address.strip()
            if address in seen:
                continue
            seen.add(address)

            record = {
                "tokenAddress": address,
                "chainId": "solana",
                "discovery_sources": [source],
            }
            name = item.get("name") or item.get("description")
            symbol = item.get("symbol")
            if isinstance(name, str) and name.strip():
                record["name"] = name.strip()
            if isinstance(symbol, str) and symbol.strip():
                record["symbol"] = symbol.strip()

            for source_key, target_key in (
                ("url", "dexscreener_url"),
                ("icon", "icon"),
                ("totalAmount", "boost_amount"),
                ("amount", "boost_amount"),
            ):
                value = item.get(source_key)
                if value is not None and target_key not in record:
                    record[target_key] = value
            found.append(record)

        print("DEXSCREENER", source, "TOKENS:", len(found), flush=True)
    except Exception as error:
        print("DEXSCREENER DISCOVERY SOURCE ERROR:", source, repr(error), flush=True)
    return found


def scan_profiles():
    return _scan_feed("dexscreener_profiles", PROFILES_URL)


def scan_latest_boosts():
    return _scan_feed("dexscreener_latest_boosts", LATEST_BOOSTS_URL)


def scan_top_boosts():
    # ScreenerBot applies chainId=solana at the API boundary for this endpoint.
    return _scan_feed("dexscreener_top_boosts", TOP_BOOSTS_URL, chain_filter=True)


def scan_dexscreener():
    """Backward-compatible combined scan; production aggregation uses three tasks."""
    found = {}
    for scanner in (scan_profiles, scan_latest_boosts, scan_top_boosts):
        for token in scanner():
            address = token["tokenAddress"]
            if address not in found:
                found[address] = token
                continue
            current = found[address]
            for source in token.get("discovery_sources", []):
                if source not in current["discovery_sources"]:
                    current["discovery_sources"].append(source)
            for key, value in token.items():
                if key not in current and value is not None:
                    current[key] = value
    result = list(found.values())
    print("TROJAN V2 DEXSCREENER TOKENS:", len(result), flush=True)
    return result


if __name__ == "__main__":
    scan_dexscreener()
