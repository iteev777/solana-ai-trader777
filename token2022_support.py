"""Read-only Token-2022 mint extension inspection and transfer-fee arithmetic.

Only extensions whose effects on swap transfer amounts are understood are
accepted. Unknown or transfer-blocking extensions fail closed.
"""

TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# SPL Token-2022 ExtensionType values from the public Token-2022 interface.
TRANSFER_FEE_CONFIG = 1
MINT_CLOSE_AUTHORITY = 3
CONFIDENTIAL_TRANSFER_MINT = 4
DEFAULT_ACCOUNT_STATE = 6
NON_TRANSFERABLE = 9
INTEREST_BEARING_CONFIG = 10
PERMANENT_DELEGATE = 12
TRANSFER_HOOK = 14
CONFIDENTIAL_TRANSFER_FEE_CONFIG = 16
METADATA_POINTER = 18
TOKEN_METADATA = 19
GROUP_POINTER = 20
TOKEN_GROUP = 21
GROUP_MEMBER_POINTER = 22
TOKEN_GROUP_MEMBER = 23
CONFIDENTIAL_MINT_BURN = 24
SCALED_UI_AMOUNT_CONFIG = 25
PAUSABLE_CONFIG = 26
PERMISSIONED_BURN = 28

# These extensions are valid on mints but require transfer or settlement
# semantics that this quote-only adapter does not model.
_UNSUPPORTED_MINT_EXTENSIONS = {
    CONFIDENTIAL_TRANSFER_MINT,
    NON_TRANSFERABLE,
    CONFIDENTIAL_TRANSFER_FEE_CONFIG,
    CONFIDENTIAL_MINT_BURN,
    PERMISSIONED_BURN,
}
_SAFE_MINT_EXTENSIONS = {
    MINT_CLOSE_AUTHORITY,
    INTEREST_BEARING_CONFIG,
    PERMANENT_DELEGATE,
    METADATA_POINTER,
    TOKEN_METADATA,
    GROUP_POINTER,
    TOKEN_GROUP,
    GROUP_MEMBER_POINTER,
    TOKEN_GROUP_MEMBER,
    SCALED_UI_AMOUNT_CONFIG,
}


def _u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little")


def _u64(data, offset):
    return int.from_bytes(data[offset:offset + 8], "little")


def _parse_transfer_fee_config(value):
    if len(value) != 108:
        raise ValueError("Token-2022 TransferFeeConfig must be exactly 108 bytes")

    schedules = []
    for label, offset in (("older", 72), ("newer", 90)):
        epoch = _u64(value, offset)
        maximum_fee = _u64(value, offset + 8)
        basis_points = _u16(value, offset + 16)
        if basis_points > 10_000:
            raise ValueError("Token-2022 transfer fee basis points exceed 10000")
        schedules.append({
            "schedule": label,
            "epoch": epoch,
            "maximum_fee": maximum_fee,
            "basis_points": basis_points,
        })
    return schedules


def inspect_mint_account(account):
    """Return a safe transfer profile for a legacy SPL or Token-2022 mint."""
    if not isinstance(account, dict):
        raise ValueError("Mint account data is missing")
    owner = account.get("owner")
    data = account.get("data")
    if not isinstance(data, (bytes, bytearray)) or len(data) < 82:
        raise ValueError("Mint account data is truncated")

    if owner == TOKEN_PROGRAM_ID:
        return {
            "token_program": "spl_token",
            "is_token_2022": False,
            "transfer_fee_schedules": [],
            "transfer_hook": False,
            "permanent_delegate": False,
            "interest_bearing": False,
            "paused": False,
            "extensions": [],
        }
    if owner != TOKEN_2022_PROGRAM_ID:
        raise ValueError("Mint is owned by an unsupported token program")

    profile = {
        "token_program": "token_2022",
        "is_token_2022": True,
        "transfer_fee_schedules": [],
        "transfer_hook": False,
        "permanent_delegate": False,
        "interest_bearing": False,
        "paused": False,
        "extensions": [],
    }
    if len(data) == 82:
        return profile
    if len(data) < 166:
        raise ValueError("Token-2022 mint extension header is truncated")
    if data[165] != 1:  # AccountType::Mint
        raise ValueError("Token-2022 extension account type is not Mint")

    cursor = 166
    seen = set()
    while cursor < len(data):
        remaining = data[cursor:]
        if not remaining or all(byte == 0 for byte in remaining):
            break
        if cursor + 4 > len(data):
            raise ValueError("Token-2022 TLV header is truncated")
        extension_type = _u16(data, cursor)
        length = _u16(data, cursor + 2)
        cursor += 4
        if extension_type == 0:
            if length == 0:
                break
            raise ValueError("Token-2022 TLV contains an invalid uninitialized entry")
        if extension_type in seen:
            raise ValueError("Token-2022 mint contains a duplicate extension")
        seen.add(extension_type)
        end = cursor + length
        if end > len(data):
            raise ValueError("Token-2022 extension value is truncated")
        value = bytes(data[cursor:end])
        cursor = end
        profile["extensions"].append(extension_type)

        if extension_type in _UNSUPPORTED_MINT_EXTENSIONS:
            raise ValueError("Token-2022 mint has an unsupported confidential or transfer-restricting extension")
        if extension_type == TRANSFER_FEE_CONFIG:
            profile["transfer_fee_schedules"] = _parse_transfer_fee_config(value)
        elif extension_type == TRANSFER_HOOK:
            if len(value) != 64:
                raise ValueError("Token-2022 TransferHook extension has an invalid size")
            hook_program = value[32:64]
            if any(hook_program):
                raise ValueError("Active Token-2022 transfer hooks are not supported by direct quotes")
            profile["transfer_hook"] = False
        elif extension_type == DEFAULT_ACCOUNT_STATE:
            if len(value) != 1 or value[0] not in (1, 2):
                raise ValueError("Token-2022 default account state is invalid")
            if value[0] == 2:
                raise ValueError("Token-2022 mint defaults token accounts to Frozen")
        elif extension_type == PAUSABLE_CONFIG:
            if len(value) != 33 or value[32] not in (0, 1):
                raise ValueError("Token-2022 PausableConfig extension has an invalid layout")
            profile["paused"] = bool(value[32])
            if profile["paused"]:
                raise ValueError("Token-2022 mint is currently paused")
        elif extension_type == PERMANENT_DELEGATE:
            if len(value) != 32:
                raise ValueError("Token-2022 PermanentDelegate extension has an invalid size")
            profile["permanent_delegate"] = any(value)
        elif extension_type == INTEREST_BEARING_CONFIG:
            profile["interest_bearing"] = True
        elif extension_type in _SAFE_MINT_EXTENSIONS:
            # These extensions do not alter raw token transfer amounts.
            pass
        else:
            raise ValueError("Token-2022 mint contains an unrecognized extension type")

    return profile


def transfer_fee_for_amount(amount, profile):
    """Conservative maximum fee across both configured epoch schedules."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0 or amount > 2**64 - 1:
        raise ValueError("Token transfer amount must be a u64 integer")
    if not isinstance(profile, dict):
        raise ValueError("Token-2022 transfer profile is missing")

    fees = []
    for schedule in profile.get("transfer_fee_schedules", []):
        basis_points = schedule.get("basis_points")
        maximum_fee = schedule.get("maximum_fee")
        if (
            isinstance(basis_points, bool) or not isinstance(basis_points, int)
            or not 0 <= basis_points <= 10_000
            or isinstance(maximum_fee, bool) or not isinstance(maximum_fee, int)
            or not 0 <= maximum_fee <= 2**64 - 1
        ):
            raise ValueError("Token-2022 transfer fee schedule is invalid")
        raw_fee = (amount * basis_points + 9_999) // 10_000 if amount and basis_points else 0
        fees.append(min(raw_fee, maximum_fee))
    return max(fees, default=0)


def apply_transfer_fees(amount_in, gross_output, input_profile, output_profile):
    """Return net pool input/output and separate transfer-fee amounts."""
    if isinstance(gross_output, bool) or not isinstance(gross_output, int) or not 0 <= gross_output <= 2**64 - 1:
        raise ValueError("Gross token output must be a u64 integer")
    input_fee = transfer_fee_for_amount(amount_in, input_profile)
    output_fee = transfer_fee_for_amount(gross_output, output_profile)
    net_input = amount_in - input_fee
    net_output = gross_output - output_fee
    if net_input < 0 or net_output < 0:
        raise ValueError("Token-2022 transfer fee exceeds the transfer amount")
    return {
        "net_input": net_input,
        "net_output": net_output,
        "input_fee": input_fee,
        "output_fee": output_fee,
    }
