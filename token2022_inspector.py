"""Token-2022 extension inspection and audited swap-compatibility checks.

General inspection never authorizes a swap. The explicit compatibility helper
allows the supported transfer-fee extension and selected metadata extensions,
while rejecting transfer hooks and unmodelled transfer/account behavior.
Protocol builders must still validate their own pool and token-account layouts.
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



def decode_transfer_fee_config(raw):
    """Decode both epoch schedules from the Token-2022 TransferFeeConfig TLV.

    The active schedule is selected by the current epoch at quote time. This
    diagnostic decoder intentionally returns both schedules and never grants
    swap approval.
    """
    if isinstance(raw, (bytes, bytearray)) and len(raw) == 82:
        return None
    extensions = parse_tlv_extensions(raw, MINT_ACCOUNT_TYPE)
    offset = TLV_START
    for extension in extensions:
        extension_type = int.from_bytes(raw[offset:offset + 2], "little")
        value_length = int.from_bytes(raw[offset + 2:offset + 4], "little")
        value_start = offset + 4
        value_end = value_start + value_length
        if extension_type == 1:
            if value_length != 108:
                raise Token2022LayoutError(
                    "TransferFeeConfig extension must contain exactly 108 bytes"
                )
            value = raw[value_start:value_end]

            def schedule(start):
                epoch = int.from_bytes(value[start:start + 8], "little")
                maximum_fee = int.from_bytes(value[start + 8:start + 16], "little")
                basis_points = int.from_bytes(value[start + 16:start + 18], "little")
                if basis_points > 10_000:
                    raise Token2022LayoutError(
                        "TransferFeeConfig basis points exceed 10000"
                    )
                return {
                    "epoch": epoch,
                    "maximum_fee": maximum_fee,
                    "basis_points": basis_points,
                }

            return {
                "older": schedule(72),
                "newer": schedule(90),
                "epoch_selection_required": True,
            }
        offset = value_end
    return None




def select_transfer_fee_schedule(config, epoch):
    """Select the active Token-2022 transfer fee schedule for a chain epoch."""
    if not isinstance(config, dict) or not isinstance(config.get("older"), dict) or not isinstance(config.get("newer"), dict):
        raise ValueError("A decoded older/newer transfer-fee configuration is required")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ValueError("Current epoch must be a non-negative integer")
    older = config["older"]
    newer = config["newer"]
    activation_epoch = newer.get("epoch")
    if isinstance(activation_epoch, bool) or not isinstance(activation_epoch, int) or activation_epoch < 0:
        raise ValueError("Newer transfer-fee activation epoch is invalid")
    return dict(newer if epoch >= activation_epoch else older)


def transfer_fee_for_amount(schedule, amount):
    """Return the Token-2022 transfer fee, rounded up and capped."""
    if not isinstance(schedule, dict):
        raise ValueError("A decoded transfer-fee schedule is required")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValueError("Transfer amount must be a non-negative integer")
    basis_points = schedule.get("basis_points")
    maximum_fee = schedule.get("maximum_fee")
    if (isinstance(basis_points, bool) or not isinstance(basis_points, int)
            or not 0 <= basis_points <= 10_000):
        raise ValueError("Transfer-fee basis points are invalid")
    if (isinstance(maximum_fee, bool) or not isinstance(maximum_fee, int)
            or maximum_fee < 0):
        raise ValueError("Transfer-fee maximum is invalid")
    if amount == 0 or basis_points == 0:
        return 0
    calculated = (amount * basis_points + 9_999) // 10_000
    return min(amount, maximum_fee, calculated)


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

def validate_swap_compatible_mint_data(raw):
    """Validate mint extensions supported by the native swap builders.

    TransferFeeConfig is allowed and returned for conservative quote math.
    Transfer hooks, confidential transfers, pausable/non-transferable behavior,
    and all other unimplemented mint extensions remain hard blockers.
    """
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 82 or raw[45] != 1:
        raise Token2022LayoutError("Token-2022 mint base state is invalid or uninitialized")
    if len(raw) == 82:
        return {"extensions": [], "transfer_fee_config": None}
    extensions = parse_tlv_extensions(raw, MINT_ACCOUNT_TYPE)
    allowed = {1, 18, 19, 20, 21, 22, 23}
    if any(extension["type"] not in allowed for extension in extensions):
        raise Token2022LayoutError("Token-2022 mint has an extension unsupported by native swap builders")
    transfer_config = decode_transfer_fee_config(raw)
    return {"extensions": extensions, "transfer_fee_config": transfer_config}


def validate_zero_fee_mint_data(raw):
    """Retain the strict zero-fee gate for protocol paths not yet fee-aware."""
    result = validate_swap_compatible_mint_data(raw)
    transfer_config = result["transfer_fee_config"]
    if transfer_config is not None:
        for schedule_name in ("older", "newer"):
            schedule = transfer_config[schedule_name]
            if schedule["basis_points"] != 0 or schedule["maximum_fee"] != 0:
                raise Token2022LayoutError("Token-2022 transfer fees must be zero for this native swap path")
        offset = TLV_START
        while offset + 4 <= len(raw):
            extension_type = int.from_bytes(raw[offset:offset + 2], "little")
            value_length = int.from_bytes(raw[offset + 2:offset + 4], "little")
            if extension_type == 0:
                break
            value_start = offset + 4
            value_end = value_start + value_length
            if value_end > len(raw):
                raise Token2022LayoutError("Token-2022 transfer fee TLV is truncated")
            if extension_type == 1:
                value = raw[value_start:value_end]
                if len(value) != 108 or any(value[:32]):
                    raise Token2022LayoutError("Token-2022 transfer fee authority must be permanently disabled")
                break
            offset = value_end
    return result


def validate_token2022_account_data(account, mint, authority, label="Token-2022 account",
                                    min_balance=0):
    """Validate a Token-2022 token account and return its spendable raw balance."""
    if not isinstance(account, dict) or account.get("owner") != chain.TOKEN_2022_PROGRAM:
        raise Token2022LayoutError(label + " is not owned by Token-2022")
    raw = account.get("data", b"")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 165:
        raise Token2022LayoutError(label + " base token-account layout is truncated")
    if chain._key(raw, 0) != mint:
        raise Token2022LayoutError(label + " mint does not match")
    if chain._key(raw, 32) != authority:
        if "vault" in label.lower():
            raise Token2022LayoutError(label + " vault token authority does not match")
        if "input token account" in label.lower() or "output token account" in label.lower():
            raise Token2022LayoutError(label + " is not owned by the signing wallet")
        raise Token2022LayoutError(label + " authority does not match")
    if raw[108] != 1:
        raise Token2022LayoutError(label + " is not initialized or is frozen")
    if len(raw) > 165:
        extensions = parse_tlv_extensions(raw, TOKEN_ACCOUNT_TYPE)
        if any(extension["type"] not in {2, 7} for extension in extensions):
            raise Token2022LayoutError(label + " has an unsupported Token-2022 account extension")
        expected_lengths = {2: 8, 7: 0}
        for extension in extensions:
            if extension["length"] != expected_lengths[extension["type"]]:
                raise Token2022LayoutError(
                    label + " has an invalid " + extension["name"] + " extension length"
                )
    balance = int.from_bytes(raw[64:72], "little")
    if isinstance(min_balance, bool) or not isinstance(min_balance, int) or min_balance < 0:
        raise ValueError("Minimum token balance must be a non-negative integer")
    if balance < min_balance:
        raise Token2022LayoutError(label + " has insufficient spendable token balance")
    return balance


def validate_token_account_for_program(account, token_program, mint, authority,
                                       label="token account", min_balance=0):
    """Validate legacy SPL Token or the audited Token-2022 account extension subset."""
    if token_program == chain.TOKEN_2022_PROGRAM:
        return validate_token2022_account_data(
            account, mint, authority, label, min_balance
        )
    if token_program != chain.TOKEN_PROGRAM:
        raise Token2022LayoutError(label + " uses an unsupported token program")
    if not isinstance(account, dict) or account.get("owner") != token_program:
        raise Token2022LayoutError(label + " token program owner does not match")
    raw = account.get("data", b"")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 165:
        raise Token2022LayoutError(label + " legacy token-account layout is invalid")
    if chain._key(raw, 0) != mint:
        raise Token2022LayoutError(label + " mint does not match")
    if chain._key(raw, 32) != authority:
        if "vault" in label.lower():
            raise Token2022LayoutError(label + " vault token authority does not match")
        if "input token account" in label.lower() or "output token account" in label.lower():
            raise Token2022LayoutError(label + " is not owned by the signing wallet")
        raise Token2022LayoutError(label + " authority does not match")
    if raw[108] != 1:
        raise Token2022LayoutError(label + " is not initialized or is frozen")
    balance = int.from_bytes(raw[64:72], "little")
    if isinstance(min_balance, bool) or not isinstance(min_balance, int) or min_balance < 0:
        raise ValueError("Minimum token balance must be a non-negative integer")
    if balance < min_balance:
        raise Token2022LayoutError(label + " has insufficient balance")
    return balance


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
        "transfer_fee_config": decode_transfer_fee_config(raw),
        "swap_eligibility": "BLOCKED_UNTIL_PROTOCOL_SPECIFIC_TOKEN_2022_SUPPORT",
        "execution_enabled": False,
        "signing": "NOT_PERFORMED",
        "broadcast": "NOT_PERFORMED",
    }


def conservative_transfer_fee_for_amount(config, amount):
    """Return the larger fee charged by either epoch schedule for this amount.

    The schedule with the larger basis-point rate is not always the more
    conservative one because each schedule also has an independent fee cap.
    Compare the actual rounded-and-capped fee for the requested raw amount.
    """
    if config is None:
        return 0
    if not isinstance(config, dict):
        raise ValueError("A decoded transfer-fee configuration is required")
    fees = []
    for name in ("older", "newer"):
        schedule = config.get(name)
        if not isinstance(schedule, dict):
            raise ValueError("Both older and newer transfer-fee schedules are required")
        fees.append(transfer_fee_for_amount(schedule, amount))
    return max(fees)


def conservative_net_amount(config, amount):
    """Return a safe lower bound for the amount received after transfer fees."""
    fee = conservative_transfer_fee_for_amount(config, amount)
    return amount - fee


def token_account_size_for_mint(raw):
    """Return the Token-2022 ATA data length for the supported mint extensions.

    The Associated Token Program adds ImmutableOwner to Token-2022 ATAs. A
    TransferFeeConfig mint additionally requires TransferFeeAmount on its
    token account. Unsupported mint extensions fail closed rather than using
    the legacy 165-byte account size.
    """
    result = validate_swap_compatible_mint_data(raw)
    # Base token account (165) + account-type byte + ImmutableOwner TLV (4).
    size = 165 + 1 + 4
    if result["transfer_fee_config"] is not None:
        # TransferFeeAmount TLV: 4-byte header + 8-byte withheld amount.
        size += 4 + 8
    return size
