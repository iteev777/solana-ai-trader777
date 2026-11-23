"""Read-only on-chain mint and token-account concentration diagnostics.

Concentration is explicitly account-authority concentration, not beneficial-owner
concentration: pool vaults and custody accounts can dominate the largest accounts.
"""
import time

TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}


def _raw_amount(value):
    if isinstance(value, str):
        if not value.isascii() or not value.isdecimal() or len(value) > 20:
            return None
        value = int(value)
    if type(value) is int and 0 <= value <= 2**64 - 1:
        return value
    return None


def get_token_security_report(mint, largest_limit=20):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("Mint address is required")
    import solana_onchain as chain
    mint = mint.strip()
    if not 32 <= len(mint) <= 44 or len(chain._b58decode(mint)) != 32:
        raise ValueError("Mint address is not a valid Solana public key")

    mint_result = chain._rpc(
        "getAccountInfo",
        [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}]
    )
    mint_account = (mint_result or {}).get("value")
    if not isinstance(mint_account, dict):
        raise ValueError("Mint account not found on-chain")
    token_program = mint_account.get("owner")
    if token_program not in TOKEN_PROGRAMS:
        raise ValueError("Mint account owner is not a supported SPL Token program")
    data = mint_account.get("data")
    account = data.get("parsed") if isinstance(data, dict) else None
    if not isinstance(account, dict) or account.get("type", "mint") != "mint":
        raise ValueError("Account is not a parsed mint")
    parsed = account.get("info")
    if not isinstance(parsed, dict) or parsed.get("isInitialized") is not True:
        raise ValueError("Mint account is not initialized or parsed")
    if "mintAuthority" not in parsed or "freezeAuthority" not in parsed:
        raise ValueError("Mint authority fields are missing from parsed RPC data")

    mint_authority = parsed.get("mintAuthority")
    freeze_authority = parsed.get("freezeAuthority")
    decimals = parsed.get("decimals")
    if type(decimals) is not int or not 0 <= decimals <= 18:
        raise ValueError("Mint decimals are invalid")

    if "supply" in parsed:
        supply_raw = _raw_amount(parsed["supply"])
    else:
        supply_result = chain._rpc(
            "getTokenSupply", [mint, {"commitment": "confirmed"}]
        )
        supply_info = (supply_result or {}).get("value") or {}
        if supply_info.get("decimals", decimals) != decimals:
            raise ValueError("Mint supply decimals do not match the mint account")
        supply_raw = _raw_amount(supply_info.get("amount"))
    if not supply_raw:
        raise ValueError("Mint supply is unavailable or zero")

    largest_result = chain._rpc(
        "getTokenLargestAccounts",
        [mint, {"commitment": "confirmed"}]
    )
    accounts = (largest_result or {}).get("value") or []
    if not isinstance(accounts, list):
        raise ValueError("Largest token account response is invalid")
    accounts = accounts[:max(1, min(int(largest_limit), 20))]
    amounts = []
    seen_addresses = set()
    for item in accounts:
        if not isinstance(item, dict):
            continue
        amount = _raw_amount(item.get("amount"))
        address = item.get("address")
        if amount is None or not isinstance(address, str) or not address:
            continue
        if address in seen_addresses:
            raise ValueError("Duplicate address in largest token accounts")
        seen_addresses.add(address)
        amounts.append({"address": address, "amount": amount})
    if not amounts:
        raise ValueError("Largest token account data unavailable")

    amounts.sort(key=lambda item: item["amount"], reverse=True)
    if sum(item["amount"] for item in amounts) > supply_raw:
        raise ValueError("Largest token account amounts exceed the observed mint supply")

    top10_raw = sum(item["amount"] for item in amounts[:10])
    top10_pct = min(100.0, top10_raw / supply_raw * 100.0)

    owner_concentration = None
    owner_amounts = {}
    account_details = [
        {
            "address": item["address"],
            "amount": item["amount"],
            "supply_pct": round(item["amount"] / supply_raw * 100.0, 6),
            "authority": None,
        }
        for item in amounts
    ]
    try:
        owner_result = chain._rpc(
            "getMultipleAccounts",
            [[item["address"] for item in amounts],
             {"encoding": "jsonParsed", "commitment": "confirmed"}]
        )
        owner_accounts = (owner_result or {}).get("value") or []
        owner_amounts = {}
        for index, (item, account) in enumerate(zip(amounts, owner_accounts)):
            info = (
                (((account or {}).get("data") or {}).get("parsed") or {})
                .get("info") or {}
            )
            owner = info.get("owner")
            if isinstance(owner, str) and owner:
                owner_amounts[owner] = owner_amounts.get(owner, 0) + item["amount"]
                account_details[index]["authority"] = owner
        if owner_amounts:
            owner_concentration = min(
                100.0, max(owner_amounts.values()) / supply_raw * 100.0
            )
    except Exception:
        # Account-level data remains useful; owner aggregation is optional.
        owner_concentration = None

    warnings = []
    if mint_authority is not None:
        warnings.append("MINT_AUTHORITY_ACTIVE")
    if freeze_authority is not None:
        warnings.append("FREEZE_AUTHORITY_ACTIVE")
    if top10_pct >= 80:
        warnings.append("TOP10_TOKEN_ACCOUNTS_HIGH_CONCENTRATION")
    elif top10_pct >= 50:
        warnings.append("TOP10_TOKEN_ACCOUNTS_ELEVATED_CONCENTRATION")

    return {
        "mint": mint,
        "token_program": token_program,
        "decimals": decimals,
        "supply_raw": supply_raw,
        "supply": supply_raw / (10 ** decimals),
        "mint_authority": "DISABLED" if mint_authority is None else "ACTIVE",
        "freeze_authority": "DISABLED" if freeze_authority is None else "ACTIVE",
        "top10_token_account_pct": round(top10_pct, 4),
        "largest_token_accounts": len(amounts),
        "largest_token_account_details": account_details,
        "top_owner_account_authorities": sorted(owner_amounts) if owner_concentration is not None else [],
        "top_owner_account_authority_pct": (
            round(owner_concentration, 4)
            if owner_concentration is not None else None
        ),
        "concentration_note": (
            "Token-account authority concentration only; pool vaults and custody "
            "accounts may be included and this is not beneficial-owner concentration."
        ),
        "warnings": warnings,
        "checked_at": time.time(),
        "commitment": "confirmed",
        "source": "solana_rpc",
        "safe_authorities": mint_authority is None and freeze_authority is None,
    }


def evaluate_token_security(report):
    if not isinstance(report, dict) or report.get("source") != "solana_rpc":
        return {"allowed": False, "reason": "ONCHAIN_SECURITY_REPORT_MISSING"}
    if report.get("mint_authority") != "DISABLED":
        return {"allowed": False, "reason": "MINT_AUTHORITY_ACTIVE_OR_UNKNOWN"}
    if report.get("freeze_authority") != "DISABLED":
        return {"allowed": False, "reason": "FREEZE_AUTHORITY_ACTIVE_OR_UNKNOWN"}
    return {
        "allowed": True,
        "reason": "ONCHAIN_AUTHORITIES_REVOKED",
        "warnings": list(report.get("warnings") or []),
    }
