"""Read-only Token-2022 mint extension inspection.

This module deliberately does not authorize swaps. It validates the canonical
Token-2022 account layout and TLV framing, then reports extensions that require
specialized quote, account sizing, transfer, or risk handling.
"""
import base64

import solana_onchain as chain

ACCOUNT_TYPE_OFFSET = 165
TLV_START = 166
MINT_ACCOUNT_TYPE = 1
TOKEN_ACCOUNT_TYPE = 2

EXTENSIONS = {
    1: "TRANSFER_FEE_CONFIG",
    2: "TRANSFER_FEE_AMOUNT",
    3: "MINT_CLOSE_AUTHORITY",
    4: "CONFIDENTIAL_TRANSFER_MINT",
    5: "CONFIDENTIAL_TRANSFER_ACCOUNT",
    6: "DEFAULT_ACCOUNT_STATE",
    7: "IMMUTABLE_OWNER",
    8: "MEMO_TRANSFER",
    9: "NON_TRANSFERABLE",
    10: "INTEREST_BEARING_CONFIG",
    11: "CPI_GUARD",
    12: "PERMANENT_DELEGATE",
    13: "NON_TRANSFERABLE_ACCOUNT",
    14: "TRANSFER_HOOK",
    15: "TRANSFER_HOOK_ACCOUNT",
    16: "CONFIDENTIAL_TRANSFER_FEE_CONFIG",
    17: "CONFIDENTIAL_TRANSFER_FEE_AMOUNT",
    18: "METADATA_POINTER",
    19: "TOKEN_METADATA",
    20: "GROUP_POINTER",
    21: "TOKEN_GROUP",
    22: "GROUP_MEMBER_POINTER",
    23: "TOKEN_GROUP_MEMBER",
    24: "CONFIDENTIAL_MINT_BURN",
    25: "SCALED_UI_AMOUNT",
    26: "PAUSABLE",
    27: "PAUSABLE_ACCOUNT",
    28: "PERMISSIONED_BURN",
}

MINT_EXTENSION_IDS = {
    1, 3, 4, 6, 9, 10, 12, 14, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26, 28,
}
ACCOUNT_EXTENSION_IDS = {2, 5, 7, 8, 11, 13, 15, 17, 27}

HIGH_RISK = {
    1: "Transfer fees change net received amounts and require epoch-aware fee calculation.",
    4: "Confidential transfers require proof-aware client flows and encrypted balance handling.",
    5: "Confidential token accounts cannot be treated as ordinary public-balance accounts.",
    6: "Default account state may initialize accounts as frozen.",
    9: "Non-transferable mints cannot be used for ordinary swaps.",
    12: "Permanent delegate creates a mint-wide authority over token accounts.",
    14: "Transfer hooks add extra programs/accounts and may execute custom transfer logic.",
    16: "Confidential transfer fees require confidential fee-aware transfer handling.",
    25: "Scaled UI amounts change display conversion and must not be confused with raw units.",
    26: "Pausable mints may block transfers.",
    24: "Confidential mint/burn changes supply accounting and is not supported by ordinary swap flows.",
    28: "Permissioned burns require authority-specific handling.",
}


class Token2022LayoutError(ValueError):
    pass


def parse_tlv_extensions(raw, expected_account_type):
    """Parse Token-2022 TLV records with strict bounds/type/duplicate checks."""
    if not isinstance(raw, (bytes, bytearray)):
        raise Token2022LayoutError("Token-2022 account data must be bytes")
    if len(raw) < TLV_START:
        raise Token2022LayoutError("Extended Token-2022 account is shorter than its type header")
    if raw[ACCOUNT_TYPE_OFFSET] != expected_account_type:
        raise Token2022LayoutError("Token-2022 account type discriminator does not match")
    if expected_account_type == MINT_ACCOUNT_TYPE and any(raw[82:165]):
        raise Token2022LayoutError("Token-2022 mint padding is not zero-filled")

    extensions = []
    seen = set()
    offset = TLV_START
    while offset < len(raw):
        remaining = len(raw) - offset
        if remaining < 4:
            if any(raw[offset:]):
                raise Token2022LayoutError("Truncated Token-2022 TLV header")
            break
        extension_type = int.from_bytes(raw[offset:offset + 2], "little")
        value_length = int.from_bytes(raw[offset + 2:offset + 4], "little")
        if extension_type == 0:
            if any(raw[offset:]):
                raise Token2022LayoutError("Non-zero data follows Token-2022 TLV terminator")
            break
        end = offset + 4 + value_length
        if end > len(raw):
            raise Token2022LayoutError("Token-2022 TLV value exceeds account data")
        if extension_type in seen:
            raise Token2022LayoutError("Duplicate Token-2022 extension type")
        allowed = MINT_EXTENSION_IDS if expected_account_type == MINT_ACCOUNT_TYPE else ACCOUNT_EXTENSION_IDS
        if extension_type not in allowed:
            raise Token2022LayoutError("Unknown or wrong-account-type Token-2022 extension")
        seen.add(extension_type)
        extensions.append({
            "type": extension_type,
            "name": EXTENSIONS.get(extension_type, "UNKNOWN"),
            "length": value_length,
            "risk": HIGH_RISK.get(extension_type),
        })
        offset = end
    return extensions


def _read_account(address):
    result = chain._rpc("getAccountInfo", [
        address, {"encoding": "base64", "commitment": "confirmed"}
    ])
    value = (result or {}).get("value")
    if value is None:
        raise ValueError("Mint account does not exist")
    try:
        raw = base64.b64decode(value["data"][0], validate=True)
    except Exception:
        raise Token2022LayoutError("RPC returned malformed mint account data")
    return {"owner": value.get("owner"), "data": raw, "lamports": value.get("lamports")}



def inspect_token2022_account(address):
    """Inspect one Token-2022 token account without interpreting extension data."""
    if not isinstance(address, str) or not address:
        raise ValueError("Token account public key is required")
    try:
        decoded = chain._b58decode(address)
    except Exception:
        raise ValueError("Token account must be a valid Solana public key")
    if len(decoded) != 32 or chain._b58encode(decoded) != address:
        raise ValueError("Token account must be a valid Solana public key")
    account = _read_account(address)
    if account["owner"] != chain.TOKEN_2022_PROGRAM:
        raise ValueError("Account is not owned by the Token-2022 program")
    raw = account["data"]
    if len(raw) == 165:
        extensions = []
        layout = "BASE_TOKEN_ACCOUNT_NO_EXTENSIONS"
    else:
        extensions = parse_tlv_extensions(raw, TOKEN_ACCOUNT_TYPE)
        layout = "EXTENDED_TOKEN_ACCOUNT"
    if len(raw) < 165 or raw[108] not in (1, 2):
        raise Token2022LayoutError("Token-2022 token account base state is invalid")
    return {
        "status": "INSPECTED_NOT_SWAP_APPROVED",
        "address": address,
        "token_program": chain.TOKEN_2022_PROGRAM,
        "account_size": len(raw),
        "mint": chain._key(raw, 0),
        "authority": chain._key(raw, 32),
        "raw_balance": int.from_bytes(raw[64:72], "little"),
        "state": "INITIALIZED" if raw[108] == 1 else "FROZEN",
        "layout": layout,
        "extensions": extensions,
        "extension_count": len(extensions),
        "swap_eligibility": "BLOCKED_UNTIL_PROTOCOL_SPECIFIC_TOKEN_2022_SUPPORT",
        "execution_enabled": False,
        "signing": "NOT_PERFORMED",
        "broadcast": "NOT_PERFORMED",
    }

def inspect_token2022_mint(mint):
    if not isinstance(mint, str) or not mint:
        raise ValueError("Mint public key is required")
    try:
        decoded = chain._b58decode(mint)
    except Exception:
        raise ValueError("Mint must be a valid Solana public key")
    if len(decoded) != 32 or chain._b58encode(decoded) != mint:
        raise ValueError("Mint must be a valid Solana public key")

    account = _read_account(mint)
    if account["owner"] != chain.TOKEN_2022_PROGRAM:
        raise ValueError("Mint is not owned by the Token-2022 program")
    raw = account["data"]
    if len(raw) == 82:
        if raw[45] != 1:
            raise Token2022LayoutError("Token-2022 mint base state is not initialized")
        extensions = []
        layout = "BASE_MINT_NO_EXTENSIONS"
    else:
        extensions = parse_tlv_extensions(raw, MINT_ACCOUNT_TYPE)
        if len(raw) < 166 or raw[45] != 1:
            raise Token2022LayoutError("Token-2022 mint base state is invalid or uninitialized")
        layout = "EXTENDED_MINT"

    return {
        "status": "INSPECTED_NOT_SWAP_APPROVED",
        "mint": mint,
        "token_program": chain.TOKEN_2022_PROGRAM,
        "account_size": len(raw),
        "decimals": raw[44],
        "supply_raw": int.from_bytes(raw[36:44], "little"),
        "layout": layout,
        "extensions": extensions,
        "extension_count": len(extensions),
        "swap_eligibility": "BLOCKED_UNTIL_PROTOCOL_SPECIFIC_TOKEN_2022_SUPPORT",
        "execution_enabled": False,
        "signing": "NOT_PERFORMED",
        "broadcast": "NOT_PERFORMED",
    }
