"""Unsigned native PumpSwap V2 transaction preparation.

Supports exact-quote-in buys and exact-base-in sells for non-cashback,
non-mayhem, legacy SPL Token pools only. Pool/config/PDA/account relationships
are revalidated at a confirmed RPC context. Transactions are compiled unsigned
for external wallet signing and are never broadcast by this module.
"""
import base64
import struct
import time

import solana_onchain as chain
from raydium_cpmm_builder import (
    _compile_legacy_message,
    _decode_user_token_account,
    _pubkey,
    _u64,
    simulate_raydium_cpmm_transaction,
)


PUMPSWAP_PROGRAM_ID = chain.PROGRAMS["pumpswap"]
PUMP_FEES_PROGRAM_ID = chain.PUMP_FEES_PROGRAM
TOKEN_PROGRAM_ID = chain.TOKEN_PROGRAM
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
GLOBAL_CONFIG = chain.PUMPSWAP_GLOBAL_CONFIG
FEE_CONFIG = chain.PUMPSWAP_FEE_CONFIG
GLOBAL_CONFIG_BUYBACK_RECIPIENTS_OFFSET = 643
GLOBAL_CONFIG_MIN_LENGTH = 907
MAX_QUOTE_SLOT_LAG = 32

BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR = bytes([194, 171, 28, 70, 104, 77, 91, 47])
SELL_V2_DISCRIMINATOR = bytes([93, 246, 130, 60, 231, 233, 64, 178])


def _ata(owner, token_program, mint):
    return chain._find_program_address(
        [
            _pubkey(owner, "ATA owner"),
            _pubkey(token_program, "ATA token program"),
            _pubkey(mint, "ATA mint"),
        ],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )[0]


def _validate_global_config(account):
    if not isinstance(account, dict) or account.get("owner") != PUMPSWAP_PROGRAM_ID:
        raise ValueError("PumpSwap GlobalConfig is not owned by the PumpSwap program")
    raw = account.get("data", b"")
    if len(raw) < GLOBAL_CONFIG_MIN_LENGTH or raw[:8] != chain.PUMPSWAP_GLOBAL_CONFIG_DISCRIMINATOR:
        raise ValueError("PumpSwap GlobalConfig discriminator or current layout is invalid")
    recipients = [
        chain._key(raw, GLOBAL_CONFIG_BUYBACK_RECIPIENTS_OFFSET + index * 32)
        for index in range(8)
    ]
    recipients = [recipient for recipient in recipients if recipient != chain.DEFAULT_PUBKEY]
    if not recipients:
        raise ValueError("PumpSwap GlobalConfig has no configured buyback fee recipients")
    buyback_basis_points = chain._u64(raw, 899)
    if buyback_basis_points > 10_000:
        raise ValueError("PumpSwap buyback basis points are invalid")
    return {
        "disable_flags": raw[56],
        "buyback_fee_recipients": recipients,
        "buyback_basis_points": buyback_basis_points,
    }


def _validate_pool_identity(pool_id, raw, decoded):
    if len(raw) < 271:
        raise ValueError("PumpSwap pool state is truncated")
    index = int.from_bytes(raw[9:11], "little")
    creator = chain._key(raw, 11)
    canonical, _ = chain._find_program_address(
        [
            b"pool",
            index.to_bytes(2, "little"),
            _pubkey(creator, "pool creator"),
            _pubkey(decoded["base_mint"], "base mint"),
            _pubkey(decoded["quote_mint"], "quote mint"),
        ],
        PUMPSWAP_PROGRAM_ID,
    )
    if canonical != pool_id:
        raise ValueError("PumpSwap pool address does not match its canonical PDA")
    return index, creator


def build_pumpswap_v2_swap(quote, wallet, user_base_token_account,
                           user_quote_token_account,
                           max_quote_slot_lag=MAX_QUOTE_SLOT_LAG):
    """Build an unsigned PumpSwap buy_exact_quote_in_v2 or sell_v2 transaction."""
    if not isinstance(quote, dict):
        raise ValueError("A validated PumpSwap quote object is required")
    if quote.get("protocol") != "pumpswap" or quote.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT":
        raise ValueError("Quote is not a confirmed PumpSwap quote")
    pool_id = quote.get("pool_id")
    input_mint = quote.get("input_mint")
    output_mint = quote.get("output_mint")
    amount_in = _u64(quote.get("input_amount"), "quote input amount")
    slippage = quote.get("slippage_bps", 100)
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 300:
        raise ValueError("Quote slippage must be an integer between 0 and 300 bps")
    quote_slot = quote.get("slot")
    if isinstance(quote_slot, bool) or not isinstance(quote_slot, int) or quote_slot <= 0:
        raise ValueError("Quote must include a confirmed positive slot")
    if isinstance(max_quote_slot_lag, bool) or not isinstance(max_quote_slot_lag, int) or not 0 <= max_quote_slot_lag <= 150:
        raise ValueError("Maximum quote slot lag is outside the supported range")

    for value, label in (
        (pool_id, "pool"), (input_mint, "input mint"), (output_mint, "output mint"),
        (wallet, "wallet"), (user_base_token_account, "user base token account"),
        (user_quote_token_account, "user quote token account"),
    ):
        _pubkey(value, label)
    if user_base_token_account == user_quote_token_account:
        raise ValueError("User base and quote token accounts must differ")

    first = chain._account(pool_id)
    if first.get("owner") != PUMPSWAP_PROGRAM_ID:
        raise ValueError("Pool account is not owned by the PumpSwap program")
    decoded = chain._decode_pool("pumpswap", first["data"])
    _validate_pool_identity(pool_id, first["data"], decoded)
    if decoded.get("is_mayhem_mode") or decoded.get("is_cashback_coin") or decoded.get("is_holder_reward"):
        raise ValueError("PumpSwap native V2 builder currently refuses mayhem, cashback and holder-reward pools")
    is_buy = input_mint == decoded["quote_mint"] and output_mint == decoded["base_mint"]
    is_sell = input_mint == decoded["base_mint"] and output_mint == decoded["quote_mint"]
    if not (is_buy or is_sell):
        raise ValueError("Quote mints do not describe a supported PumpSwap buy or sell")

    fresh = chain.quote_pumpswap_pool(pool_id, input_mint, amount_in, slippage)
    if (not isinstance(fresh, dict)
            or fresh.get("status") != "QUOTED_FROM_CONFIRMED_ONCHAIN_SNAPSHOT"
            or fresh.get("pool_id") != pool_id
            or fresh.get("input_mint") != input_mint
            or fresh.get("output_mint") != output_mint
            or fresh.get("input_amount") != amount_in):
        raise ValueError("Fresh PumpSwap quote did not validate the requested pool, pair and amount")
    fresh_out = _u64(fresh.get("out_amount"), "fresh quote output")
    minimum_out = _u64(fresh.get("minimum_out_amount"), "fresh quote minimum output")
    if minimum_out > fresh_out:
        raise ValueError("Fresh PumpSwap minimum output exceeds its quoted output")
    fresh_slot = fresh.get("slot")
    if isinstance(fresh_slot, bool) or not isinstance(fresh_slot, int) or fresh_slot < quote_slot:
        raise ValueError("Fresh PumpSwap quote is older than the supplied quote")

    if input_mint not in (decoded["base_mint"], decoded["quote_mint"]):
        raise ValueError("Input mint does not belong to the PumpSwap pool")
    if output_mint != (decoded["base_mint"] if input_mint == decoded["quote_mint"] else decoded["quote_mint"]):
        raise ValueError("Output mint does not match the opposite PumpSwap pool mint")

    global_config_pda, _ = chain._find_program_address([b"global_config"], PUMPSWAP_PROGRAM_ID)
    fee_config_pda, _ = chain._find_program_address(
        [b"fee_config", _pubkey(PUMPSWAP_PROGRAM_ID, "PumpSwap program")],
        PUMP_FEES_PROGRAM_ID,
    )
    user_volume_accumulator, _ = chain._find_program_address(
        [b"user_volume_accumulator", _pubkey(wallet, "wallet")],
        PUMPSWAP_PROGRAM_ID,
    )
    event_authority, _ = chain._find_program_address([b"__event_authority"], PUMPSWAP_PROGRAM_ID)
    if global_config_pda != GLOBAL_CONFIG or fee_config_pda != FEE_CONFIG:
        raise ValueError("PumpSwap canonical GlobalConfig or FeeConfig PDA does not match known address")

    # The first account read discovers the pool relationships. The confirmed
    # multi-account snapshot below includes the global recipient list and all
    # candidate buyback ATAs, so the selected recipient is live-configured.
    pre_global = chain._account(global_config_pda)
    global_state = _validate_global_config(pre_global)
    buyback_candidates = [
        (recipient, _ata(recipient, TOKEN_PROGRAM_ID, decoded["quote_mint"]))
        for recipient in global_state["buyback_fee_recipients"]
    ]
    keys = [
        pool_id, global_config_pda, fee_config_pda,
        decoded["vault_base"], decoded["vault_quote"],
        decoded["base_mint"], decoded["quote_mint"],
        user_base_token_account, user_quote_token_account,
        user_volume_accumulator,
    ] + [ata for _, ata in buyback_candidates]
    accounts, slot = chain._multiple_accounts_with_context(keys)
    if slot < fresh_slot or slot - fresh_slot > max_quote_slot_lag:
        raise ValueError("Fresh PumpSwap quote is stale relative to the transaction snapshot")
    if any(account is None for account in accounts[:9]):
        raise ValueError("PumpSwap confirmed transaction snapshot is missing a required account")
    (pool_acc, global_acc, fee_acc, vault_base_acc, vault_quote_acc,
     mint_base_acc, mint_quote_acc, user_base_acc, user_quote_acc,
     user_volume_acc, *buyback_accs) = accounts

    if pool_acc.get("owner") != PUMPSWAP_PROGRAM_ID:
        raise ValueError("PumpSwap pool owner changed during transaction snapshot")
    current = chain._decode_pool("pumpswap", pool_acc["data"])
    _validate_pool_identity(pool_id, pool_acc["data"], current)
    for field in ("base_mint", "quote_mint", "vault_base", "vault_quote",
                  "is_mayhem_mode", "is_cashback_coin", "is_holder_reward"):
        if current.get(field) != decoded.get(field):
            raise ValueError("PumpSwap pool relationships or trade mode changed during snapshot")
    if current.get("is_mayhem_mode") or current.get("is_cashback_coin") or current.get("is_holder_reward"):
        raise ValueError("PumpSwap pool mode is not supported by the native V2 builder")

    snapshot_global_state = _validate_global_config(global_acc)
    if (snapshot_global_state["buyback_fee_recipients"] != global_state["buyback_fee_recipients"]
            or snapshot_global_state["buyback_basis_points"] != global_state["buyback_basis_points"]):
        raise ValueError("PumpSwap buyback fee configuration changed during transaction preparation")
    global_state = snapshot_global_state
    if user_volume_acc is not None:
        if user_volume_acc.get("owner") != PUMPSWAP_PROGRAM_ID:
            raise ValueError("Existing user volume accumulator is not owned by PumpSwap")
        volume_raw = user_volume_acc.get("data", b"")
        if (len(volume_raw) < 41
                or volume_raw[:8] != bytes([86, 255, 112, 14, 102, 53, 154, 250])
                or chain._key(volume_raw, 8) != wallet):
            raise ValueError("Existing user volume accumulator has an invalid owner or layout")
    if global_state["disable_flags"] & ((1 << 3) if is_buy else (1 << 4)):
        raise ValueError("PumpSwap buy or sell instruction is disabled by GlobalConfig")
    if fee_acc.get("owner") != PUMP_FEES_PROGRAM_ID:
        raise ValueError("PumpSwap FeeConfig is not owned by the Pump Fees program")
    chain._decode_pumpswap_fee_config(fee_acc.get("data", b""))

    for mint_acc in (mint_base_acc, mint_quote_acc):
        if mint_acc.get("owner") != TOKEN_PROGRAM_ID:
            raise ValueError("PumpSwap native V2 builder currently accepts legacy SPL Token mints only")
    if vault_base_acc.get("owner") != TOKEN_PROGRAM_ID or vault_quote_acc.get("owner") != TOKEN_PROGRAM_ID:
        raise ValueError("PumpSwap pool vaults must be owned by legacy SPL Token")
    chain._token_amount(vault_base_acc, current["base_mint"])
    chain._token_amount(vault_quote_acc, current["quote_mint"])
    if (chain._key(vault_base_acc["data"], 32) != pool_id
            or chain._key(vault_quote_acc["data"], 32) != pool_id):
        raise ValueError("PumpSwap vault token authority does not match the pool PDA")
    if vault_base_acc["data"][108] != 1 or vault_quote_acc["data"][108] != 1:
        raise ValueError("PumpSwap pool vault is not initialized or is frozen")

    base_balance = _decode_user_token_account(user_base_acc, wallet, current["base_mint"], "User base token account")
    quote_balance = _decode_user_token_account(user_quote_acc, wallet, current["quote_mint"], "User quote token account")
    if is_buy and quote_balance < amount_in:
        raise ValueError("Wallet quote token account has insufficient balance")
    if is_sell and base_balance < amount_in:
        raise ValueError("Wallet base token account has insufficient balance")

    selected_buyback = None
    for (recipient, ata), account in zip(buyback_candidates, buyback_accs):
        if not account or account.get("owner") != TOKEN_PROGRAM_ID:
            continue
        raw = account.get("data", b"")
        if len(raw) < 165:
            continue
        if (chain._key(raw, 0) == current["quote_mint"]
                and chain._key(raw, 32) == recipient
                and raw[108] == 1):
            selected_buyback = (recipient, ata)
            break
    if selected_buyback is None:
        raise ValueError("No configured PumpSwap buyback recipient has an existing valid quote ATA")

    if is_buy:
        instruction_data = (
            BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR
            + struct.pack("<Q", amount_in)
            + struct.pack("<Q", minimum_out)
        )
        instruction = "buy_exact_quote_in_v2"
    else:
        instruction_data = (
            SELL_V2_DISCRIMINATOR
            + struct.pack("<Q", amount_in)
            + struct.pack("<Q", minimum_out)
        )
        instruction = "sell_v2"

    metas = [
        {"pubkey": pool_id, "is_writable": True},
        {"pubkey": wallet, "is_signer": True, "is_writable": True},
        {"pubkey": global_config_pda},
        {"pubkey": current["base_mint"]},
        {"pubkey": current["quote_mint"]},
        {"pubkey": user_base_token_account, "is_writable": True},
        {"pubkey": user_quote_token_account, "is_writable": True},
        {"pubkey": current["vault_base"], "is_writable": True},
        {"pubkey": current["vault_quote"], "is_writable": True},
        {"pubkey": TOKEN_PROGRAM_ID},
        {"pubkey": TOKEN_PROGRAM_ID},
        {"pubkey": SYSTEM_PROGRAM_ID},
        {"pubkey": user_volume_accumulator, "is_writable": True},
        {"pubkey": fee_config_pda},
        {"pubkey": selected_buyback[1], "is_writable": True},
        {"pubkey": event_authority},
        {"pubkey": PUMPSWAP_PROGRAM_ID},
    ]
    latest = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    blockhash_value = ((latest or {}).get("value") or {}).get("blockhash")
    last_valid_height = ((latest or {}).get("value") or {}).get("lastValidBlockHeight")
    _pubkey(blockhash_value, "latest blockhash")
    if isinstance(last_valid_height, bool) or not isinstance(last_valid_height, int) or last_valid_height <= 0:
        raise ValueError("RPC latest blockhash response is missing lastValidBlockHeight")
    transaction, account_keys = _compile_legacy_message(
        wallet, blockhash_value, PUMPSWAP_PROGRAM_ID, metas, instruction_data
    )
    return {
        "provider": "pumpswap_v2_native_builder",
        "protocol": "pumpswap",
        "pool_id": pool_id,
        "slot": slot,
        "quote_slot": quote_slot,
        "fresh_quote_slot": fresh_slot,
        "quote_slot_lag": slot - fresh_slot,
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount_in,
        "minimum_output_amount": minimum_out,
        "instruction": instruction,
        "buyback_fee_recipient": selected_buyback[0],
        "buyback_fee_recipient_ata": selected_buyback[1],
        "account_keys": account_keys,
        "instruction_data_base64": base64.b64encode(instruction_data).decode("ascii"),
        "required_signers": [wallet],
        "transaction": base64.b64encode(transaction).decode("ascii"),
        "transaction_version": "legacy",
        "last_valid_block_height": last_valid_height,
        "signature_status": "UNSIGNED_EXTERNAL_WALLET_REQUIRED",
        "execution": "LOCKED",
        "signing": "DISABLED_ON_SERVER",
        "broadcast": "DISABLED",
        "live_trading_ready": False,
    }


def simulate_pumpswap_v2_transaction(transaction_base64):
    """Simulate unsigned PumpSwap transaction; no signing or broadcast."""
    return simulate_raydium_cpmm_transaction(transaction_base64)
