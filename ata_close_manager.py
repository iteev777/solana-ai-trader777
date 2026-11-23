"""Non-custodial empty ATA close orders for legacy SPL and Token-2022.

ScreenerBot signs and submits ATA closes from its configured keypair. Trojan
keeps the wallet non-custodial: it validates an empty canonical ATA, compiles
and simulates an unsigned CloseAccount transaction, and leaves signing and
broadcast to the user's wallet. No close is submitted by this module.
"""
import base64

import ata_manager
import solana_onchain as chain
import token2022_inspector as token2022
from raydium_cpmm_builder import _compile_legacy_message

SYSTEM_PROGRAM_ID = ata_manager.SYSTEM_PROGRAM_ID
ASSOCIATED_TOKEN_PROGRAM_ID = ata_manager.ASSOCIATED_TOKEN_PROGRAM_ID
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
TOKEN_2022_PROGRAM_ID = chain.TOKEN_2022_PROGRAM
SOL_MINT = "So11111111111111111111111111111111111111112"
U64_MAX = (1 << 64) - 1


def _u64(value, label, allow_zero=True):
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= U64_MAX:
        raise ValueError(label + " must be a valid u64")
    return value


def _close_authority(raw, wallet, account_owner):
    if len(raw) < 165:
        raise ValueError("Token account is truncated before its close-authority field")
    option = int.from_bytes(raw[129:133], "little")
    if option == 0:
        authority = account_owner
    elif option == 1:
        authority = chain._b58encode(raw[133:165])
    else:
        raise ValueError("Token account close-authority option is invalid")
    if authority != wallet:
        raise ValueError("Wallet is not the token account close authority")


def _withheld_transfer_fee(raw):
    if len(raw) == 165:
        return 0
    extensions = token2022.parse_tlv_extensions(raw, token2022.TOKEN_ACCOUNT_TYPE)
    offset = token2022.TLV_START
    withheld = 0
    for extension in extensions:
        extension_type = int.from_bytes(raw[offset:offset + 2], "little")
        length = int.from_bytes(raw[offset + 2:offset + 4], "little")
        start = offset + 4
        if extension_type == 2:
            if length != 8:
                raise ValueError("Token-2022 TransferFeeAmount extension has an invalid length")
            withheld = int.from_bytes(raw[start:start + 8], "little")
        offset = start + length
    return withheld


def _decode_empty_ata(wallet, token_account):
    ata_manager._pubkey(wallet, "Wallet")
    ata_manager._pubkey(token_account, "Token account")
    account = ata_manager._rpc_account(token_account)
    if account is None:
        raise ValueError("Token account does not exist")
    token_program = account.get("owner")
    if token_program not in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
        raise ValueError("Token account is not owned by a supported token program")
    raw = account.get("data")
    if not isinstance(raw, bytes) or len(raw) < 165:
        raise ValueError("Token account data is truncated or malformed")
    mint = chain._key(raw, 0)
    account_owner = chain._key(raw, 32)
    if account_owner != wallet:
        raise ValueError("Token account is not owned by the requested wallet")
    expected_ata = chain._find_program_address(
        [ata_manager._pubkey(wallet, "Wallet"),
         ata_manager._pubkey(token_program, "Token program"),
         ata_manager._pubkey(mint, "Mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )[0]
    if expected_ata != token_account:
        raise ValueError("Token account is not the wallet's canonical associated token account")

    balance = token2022.validate_token_account_for_program(
        account, token_program, mint, wallet, "ATA close candidate"
    )
    if balance != 0:
        raise ValueError("ATA must have a zero token balance before it can be closed")
    _close_authority(raw, wallet, account_owner)
    withheld = _withheld_transfer_fee(raw) if token_program == TOKEN_2022_PROGRAM_ID else 0
    if withheld:
        raise ValueError("Token-2022 ATA contains withheld transfer fees and cannot be closed")
    lamports = _u64(account.get("lamports"), "Token account lamports")
    return {
        "account": account,
        "raw": raw,
        "mint": mint,
        "token_program": token_program,
        "rent_refund_lamports": lamports,
        "withheld_transfer_fee_raw": withheld,
    }


def close_empty_ata_order(wallet, token_account, min_sol_reserve_lamports=None):
    """Build and simulate an unsigned CloseAccount order for an empty canonical ATA."""
    ata = _decode_empty_ata(wallet, token_account)
    wallet_account = ata_manager._rpc_account(wallet)
    if wallet_account is None or wallet_account.get("owner") != SYSTEM_PROGRAM_ID:
        raise ValueError("Close-order payer must be an existing system-owned wallet")
    if wallet_account.get("data") != b"":
        raise ValueError("Close-order payer must be a standard system wallet without account data")
    _u64(wallet_account.get("lamports"), "Wallet account lamports")

    if min_sol_reserve_lamports is None:
        raw_reserve = __import__("os").getenv("TROJAN_MIN_SOL_RESERVE_LAMPORTS", "10000000")
        if not raw_reserve.isdigit():
            raise ValueError("TROJAN_MIN_SOL_RESERVE_LAMPORTS must be a non-negative integer")
        reserve = _u64(int(raw_reserve), "Minimum SOL reserve")
    else:
        reserve = _u64(min_sol_reserve_lamports, "Minimum SOL reserve")

    close_instruction_data = bytes([9])  # SPL Token / Token-2022 CloseAccount
    metas = [
        {"pubkey": token_account, "is_signer": False, "is_writable": True},
        {"pubkey": wallet, "is_signer": False, "is_writable": True},
        {"pubkey": wallet, "is_signer": True, "is_writable": False},
    ]
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = (latest or {}).get("value") or {}
    blockhash = value.get("blockhash")
    last_valid = value.get("lastValidBlockHeight")
    ata_manager._pubkey(blockhash, "Latest blockhash")
    _u64(last_valid, "Last valid block height", allow_zero=False)

    transaction, account_keys = _compile_legacy_message(
        wallet, blockhash, ata["token_program"], metas, close_instruction_data,
        compute_unit_limit=None, compute_unit_price=0,
    )
    # The compiler emits one all-zero external-wallet signature. The message
    # starts after the one-byte signature-count prefix and its 64-byte slot.
    message = transaction[65:]
    message_b64 = base64.b64encode(message).decode("ascii")
    fee_result = chain._rpc("getFeeForMessage", [
        message_b64, {"commitment": "confirmed"}
    ])
    fee = _u64((fee_result or {}).get("value"), "Close transaction fee")
    balance_result = chain._rpc("getBalance", [wallet, {"commitment": "confirmed"}])
    wallet_balance = _u64((balance_result or {}).get("value"), "Wallet SOL balance")
    required = fee + reserve
    if required > U64_MAX or wallet_balance < required:
        raise ValueError("Wallet SOL balance is insufficient for close fee and configured reserve")

    transaction_b64 = base64.b64encode(transaction).decode("ascii")
    simulation = chain._rpc("simulateTransaction", [
        transaction_b64,
        {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True,
         "commitment": "confirmed", "innerInstructions": False},
    ])
    sim_value = (simulation or {}).get("value")
    if not isinstance(sim_value, dict) or "err" not in sim_value:
        raise ValueError("RPC returned a malformed ATA close simulation")
    if sim_value["err"] is not None:
        raise ValueError("ATA close simulation failed: " + str(sim_value["err"])[:160])
    logs = sim_value.get("logs", [])
    if not isinstance(logs, list):
        raise ValueError("RPC returned malformed ATA close simulation logs")
    units = sim_value.get("unitsConsumed")
    if units is not None:
        _u64(units, "Simulation compute units")

    return {
        "status": "UNSIGNED_ATA_CLOSE_ORDER_SIMULATED",
        "closed": False,
        "wallet": wallet,
        "token_account": token_account,
        "mint": ata["mint"],
        "token_program": ata["token_program"],
        "instruction": "CloseAccount",
        "transaction": transaction_b64,
        "transaction_version": "legacy",
        "required_signers": [wallet],
        "account_keys": account_keys,
        "wallet_sol_balance_lamports": wallet_balance,
        "rent_refund_lamports": ata["rent_refund_lamports"],
        "network_fee_lamports": fee,
        "minimum_sol_reserve_lamports": reserve,
        "last_valid_block_height": last_valid,
        "simulation": {
            "status": "PASS",
            "units_consumed": units,
            "logs": [str(line)[:300] for line in logs[-20:]],
        },
        "signing": "EXTERNAL_WALLET_REQUIRED",
        "broadcast": "DISABLED",
        "side_effects": "RPC_READ_AND_UNSIGNED_TRANSACTION_CONSTRUCTION_ONLY",
        "live_trading_ready": False,
    }


def close_empty_wsol_ata_order(wallet, min_sol_reserve_lamports=None):
    """Build an unsigned close order only for the canonical legacy WSOL ATA."""
    ata = chain._find_program_address(
        [ata_manager._pubkey(wallet, "Wallet"),
         ata_manager._pubkey(TOKEN_PROGRAM_ID, "Token program"),
         ata_manager._pubkey(SOL_MINT, "WSOL mint")],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )[0]
    return close_empty_ata_order(wallet, ata, min_sol_reserve_lamports)
