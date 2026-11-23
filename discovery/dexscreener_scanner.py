"""DexScreener discovery feed for newly profiled and boosted Solana tokens."""
from api_client import get_json

ENDPOINTS = [
    ("dexscreener_profiles", "https://api.dexscreener.com/token-profiles/latest/v1"),
    ("dexscreener_latest_boosts", "https://api.dexscreener.com/token-boosts/latest/v1"),
    ("dexscreener_top_boosts", "https://api.dexscreener.com/token-boosts/top/v1"),
]


def scan_dexscreener():
    found = {}
    available = False
    for source, url in ENDPOINTS:
        try:
            payload = get_json(url, timeout=10, cache_ttl=20, max_retries=1)
            if not isinstance(payload, list):
                raise ValueError("Invalid DexScreener response shape")
            available = True
            for item in payload[:100]:
                if not isinstance(item, dict) or str(item.get("chainId", "")).lower() != "solana":
                    continue
                address = item.get("tokenAddress") or item.get("address")
                if not isinstance(address, str) or not address.strip():
                    continue
                address = address.strip()
                token = found.setdefault(address, {
                    "tokenAddress": address,
                    "chainId": "solana",
                    "discovery_sources": [],
                })
                if source not in token["discovery_sources"]:
                    token["discovery_sources"].append(source)
                fields = {
                    "name": item.get("name") or item.get("description"),
                    "symbol": item.get("symbol"),
                    "dexscreener_url": item.get("url"),
                    "icon": item.get("icon"),
                    "boost_amount": item.get("totalAmount"),
                }
                if fields["boost_amount"] is None:
                    fields["boost_amount"] = item.get("amount")
                for key, value in fields.items():
                    if token.get(key) in (None, "") and value not in (None, ""):
                        token[key] = value
        except Exception as error:
            print("DEXSCREENER DISCOVERY SOURCE ERROR:", source, type(error).__name__, flush=True)
    if not available:
        raise RuntimeError("DexScreener: all discovery feeds unavailable")
    result = list(found.values())
    for token in result:
        token.setdefault("name", "Unknown")
        token.setdefault("symbol", "Unknown")
        for field in ("dexscreener_url", "icon", "boost_amount"):
            token.setdefault(field, None)
    print("TROJAN V2 DEXSCREENER TOKENS:", len(result), flush=True)
    return result


if __name__ == "__main__":
    scan_dexscreener()
