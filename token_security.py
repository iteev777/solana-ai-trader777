"""Read-only on-chain mint and token-account concentration diagnostics.

Concentration is explicitly account-authority concentration, not beneficial-owner
concentration: pool vaults and custody accounts can dominate the largest accounts.
"""
import time

TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}


def _pubkey(value, label, chain):
    if not isinstance(value, str) or not value:
        raise ValueError(label + " must be a valid Solana public key")
    try:
        decoded = chain._b58decode(value)
    except Exception:
        raise ValueError(label + " must be a valid Solana public key")
    if len(decoded) != 32 or chain._b58encode(decoded) != value:
        raise ValueError(label + " must be a valid Solana public key")
    return value


def _raw_u64(value, label):
    if isinstance(value, bool):
        raise ValueError(label + " must be a raw u64 integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.isdigit():
        result = int(value)
    else:
        raise ValueError(label + " must be a raw u64 integer")
    if not 0 <= result <= (1 << 64) - 1:
        raise ValueError(label + " is outside the u64 range")
    return result


def _authority_state(value):
    if value is None or value is False:
        return "DISABLED"
    if isinstance(value, str) and value.strip():
        return "ACTIVE"
    return "UNKNOWN"


def get_token_security_report(mint, largest_limit=20):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("Mint address is required")
    import solana_onchain as chain
    _pubkey(mint, "Mint address", chain)

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
    parsed = ((data.get("parsed") or {}).get("info") or {}) if isinstance(data, dict) else {}
    if not isinstance(parsed, dict) or parsed.get("isInitialized") is not True:
        raise ValueError("Mint account is not initialized or parsed")
    if "mintAuthority" not in parsed or "freezeAuthority" not in parsed:
        raise ValueError("Mint authority fields are missing from parsed RPC data")

    mint_authority = parsed.get("mintAuthority")
    freeze_authority = parsed.get("freezeAuthority")
    decimals = parsed.get("decimals")
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 18:
        raise ValueError("Mint decimals are invalid")

    if isinstance(largest_limit, bool) or not isinstance(largest_limit, int):
        raise ValueError("Largest-account limit must be an integer")
    limit = max(1, min(largest_limit, 20))

    supply_result = chain._rpc(
        "getTokenSupply", [mint, {"commitment": "confirmed"}]
    )
    supply_info = (supply_result or {}).get("value")
    if not isinstance(supply_info, dict):
        raise ValueError("Mint supply response is malformed")
    supply_raw = _raw_u64(supply_info.get("amount"), "Mint supply")
    if supply_raw <= 0:
        raise ValueError("Mint supply is unavailable or zero")
    supply_decimals = supply_info.get("decimals")
    if supply_decimals is not None and (
        isinstance(supply_decimals, bool) or not isinstance(supply_decimals, int)
        or supply_decimals != decimals
    ):
        raise ValueError("Mint supply decimals do not match parsed mint decimals")

    largest_result = chain._rpc(
        "getTokenLargestAccounts",
        [mint, {"commitment": "confirmed"}]
    )
    accounts = (largest_result or {}).get("value")
    if not isinstance(accounts, list):
        raise ValueError("Largest token account response is invalid")
    amounts = []
    for item in accounts[:limit]:
        if not isinstance(item, dict):
            raise ValueError("Largest token account entry is malformed")
        address = item.get("address")
        _pubkey(address, "Largest token account address", chain)
        amount = _raw_u64(item.get("amount"), "Largest token account amount")
        amounts.append({"address": address, "amount": amount})
    if not amounts:
        raise ValueError("Largest token account data unavailable")
    amounts.sort(key=lambda item: item["amount"], reverse=True)

    top10_raw = sum(item["amount"] for item in amounts[:10])
    if top10_raw > supply_raw:
        raise ValueError("Largest token account balances exceed total mint supply")
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
        owner_accounts = (owner_result or {}).get("value")
        if isinstance(owner_accounts, list) and len(owner_accounts) == len(amounts):
            owner_amounts = {}
            owners_complete = True
            for index, (item, account) in enumerate(zip(amounts, owner_accounts)):
                info = (
                    (((account or {}).get("data") or {}).get("parsed") or {})
                    .get("info") or {}
                ) if isinstance(account, dict) else {}
                owner = info.get("owner") if isinstance(info, dict) else None
                try:
                    _pubkey(owner, "Largest token account authority", chain)
                except ValueError:
                    owners_complete = False
                    continue
                owner_amounts[owner] = owner_amounts.get(owner, 0) + item["amount"]
                account_details[index]["authority"] = owner
            if owners_complete and owner_amounts:
                owner_concentration = min(
                    100.0, max(owner_amounts.values()) / supply_raw * 100.0
                )
    except Exception:
        # Account-level data remains useful; owner aggregation is optional.
        owner_concentration = None

    warnings = []
    if _authority_state(mint_authority) == "ACTIVE":
        warnings.append("MINT_AUTHORITY_ACTIVE")
    if _authority_state(freeze_authority) == "ACTIVE":
        warnings.append("FREEZE_AUTHORITY_ACTIVE")
    if top10_pct >= 80:
        warnings.append("TOP10_TOKEN_ACCOUNTS_HIGH_CONCENTRATION")
    elif top10_pct >= 50:
        warnings.append("TOP10_TOKEN_ACCOUNTS_ELEVATED_CONCENTRATION")

    return {
        "mint": mint,
        "token_program": token_program,
        "decimals": decimals,
        "supply": supply_raw / (10 ** decimals),
        "mint_authority": _authority_state(mint_authority),
        "freeze_authority": _authority_state(freeze_authority),
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
        "safe_authorities": (
            _authority_state(mint_authority) == "DISABLED"
            and _authority_state(freeze_authority) == "DISABLED"
        ),
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
