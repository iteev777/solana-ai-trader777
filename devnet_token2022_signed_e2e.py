"""Signed Solana Devnet E2E for real Token-2022 ATA creation.

Creates a one-run ephemeral payer in memory. The workflow prints only its
public address and waits for manual Devnet funding. It then simulates,
signs, submits and verifies an Associated Token Account creation for the
configured Token-2022 mint. No mainnet endpoint, user secret, or live-trading
path is used.
"""
import base64
import os
import time
from urllib.parse import urlparse

import requests
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

DEVNET_URL = "https://api.devnet.solana.com"
DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
MINT = Pubkey.from_string("CFTBSCy68VqUzGeMwjmgzufeBgGi64i281f4iUxzavj7")
TOKEN_2022_PROGRAM = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
EXPECTED_ATA_SIZE = 182
FUNDING_TIMEOUT_SECONDS = 600
FINALITY_TIMEOUT_SECONDS = 90
REQUEST_TIMEOUT_SECONDS = 20


def rpc(method, params):
    response = requests.post(
        DEVNET_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Malformed Devnet RPC response")
    if payload.get("error"):
        raise RuntimeError(method + " failed: " + str(payload["error"])[:300])
    return payload.get("result")


def require_devnet():
    parsed = urlparse(DEVNET_URL)
    if parsed.scheme != "https" or parsed.hostname != "api.devnet.solana.com":
        raise RuntimeError("This test is restricted to the official HTTPS Solana Devnet RPC")
    if rpc("getGenesisHash", []) != DEVNET_GENESIS_HASH:
        raise RuntimeError("RPC genesis hash does not match Solana Devnet")


def wait_for_funding(address, minimum_lamports):
    print(
        "TOKEN2022_FUNDING_REQUIRED: send at least "
        + str(minimum_lamports / 1_000_000_000)
        + " Devnet SOL to temporary payer "
        + str(address)
        + ". This address is one-run only; no private key is logged.",
        flush=True,
    )
    deadline = time.monotonic() + FUNDING_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = rpc("getBalance", [str(address), {"commitment": "confirmed"}])
        balance = (result or {}).get("value")
        if isinstance(balance, int) and balance >= minimum_lamports:
            return balance
        time.sleep(5)
    raise TimeoutError("Temporary Token-2022 payer was not funded within 10 minutes")


def wait_finalized(signature, last_valid_height):
    deadline = time.monotonic() + FINALITY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = rpc("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        values = (result or {}).get("value")
        status = values[0] if isinstance(values, list) and len(values) == 1 else None
        if status:
            if status.get("err") is not None:
                raise RuntimeError("Token-2022 ATA transaction failed: " + str(status["err"])[:300])
            if status.get("confirmationStatus") == "finalized":
                return status
        height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
        if isinstance(height, int) and height > last_valid_height:
            raise RuntimeError("Transaction blockhash expired before finality")
        time.sleep(2)
    raise TimeoutError("Token-2022 ATA transaction did not finalize within 90 seconds")


def read_account(address, commitment="confirmed"):
    result = rpc("getAccountInfo", [
        str(address),
        {"encoding": "base64", "commitment": commitment},
    ])
    value = (result or {}).get("value")
    if value is None:
        return None
    data = value.get("data")
    if not isinstance(data, list) or len(data) < 1:
        raise RuntimeError("RPC returned malformed account data")
    return {
        "owner": value.get("owner"),
        "lamports": value.get("lamports"),
        "data": base64.b64decode(data[0], validate=True),
    }


def main():
    if os.getenv("TROJAN_DEVNET_TOKEN2022_SIGNED_E2E_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError("Explicit opt-in is required for the signed Devnet Token-2022 test")

    require_devnet()

    # Reuse Trojan's audited Token-2022 account-layout validator when checking
    # the finalized ATA; a matching byte length alone is not sufficient.
    import token2022_inspector as token2022

    mint_account = read_account(MINT)
    if mint_account is None or mint_account["owner"] != str(TOKEN_2022_PROGRAM):
        raise RuntimeError("Configured mint is missing or not owned by Token-2022")
    if len(mint_account["data"]) != 278:
        raise RuntimeError("Configured Token-2022 mint data size changed; review before proceeding")

    payer = Keypair()
    payer_pubkey = payer.pubkey()
    ata, bump = Pubkey.find_program_address(
        [bytes(payer_pubkey), bytes(TOKEN_2022_PROGRAM), bytes(MINT)],
        ASSOCIATED_TOKEN_PROGRAM,
    )
    if read_account(ata) is not None:
        raise RuntimeError("Unexpected pre-existing ATA for one-run ephemeral owner")

    rent_lamports = rpc("getMinimumBalanceForRentExemption", [EXPECTED_ATA_SIZE])
    if not isinstance(rent_lamports, int) or rent_lamports <= 0:
        raise RuntimeError("RPC returned invalid Token-2022 ATA rent")
    # Rent plus margin for the transaction fee and safety; no more is required.
    required_funding = rent_lamports + 100_000
    starting_balance = wait_for_funding(payer_pubkey, required_funding)

    latest = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    latest_value = (latest or {}).get("value") or {}
    blockhash_text = latest_value.get("blockhash")
    last_valid_height = latest_value.get("lastValidBlockHeight")
    if not blockhash_text or not isinstance(last_valid_height, int):
        raise RuntimeError("RPC returned an invalid recent blockhash")

    # Associated Token Program CreateIdempotent instruction (data byte 1).
    accounts = [
        AccountMeta(payer_pubkey, True, True),
        AccountMeta(ata, False, True),
        AccountMeta(payer_pubkey, False, False),
        AccountMeta(MINT, False, False),
        AccountMeta(SYSTEM_PROGRAM, False, False),
        AccountMeta(TOKEN_2022_PROGRAM, False, False),
    ]
    instruction = Instruction(ASSOCIATED_TOKEN_PROGRAM, bytes([1]), accounts)
    blockhash = Hash.from_string(blockhash_text)
    message = Message.new_with_blockhash([instruction], payer_pubkey, blockhash)
    unsigned = Transaction.new_unsigned(message)
    unsigned_b64 = base64.b64encode(bytes(unsigned)).decode("ascii")

    unsigned_sim = rpc("simulateTransaction", [
        unsigned_b64,
        {
            "encoding": "base64",
            "sigVerify": False,
            "replaceRecentBlockhash": False,
            "commitment": "confirmed",
        },
    ])
    unsigned_value = (unsigned_sim or {}).get("value")
    if not isinstance(unsigned_value, dict) or unsigned_value.get("err") is not None:
        raise RuntimeError("Unsigned ATA simulation failed: " + str(unsigned_value)[:300])

    signed = Transaction.new_unsigned(message)
    signed.sign([payer], blockhash)
    signed_b64 = base64.b64encode(bytes(signed)).decode("ascii")
    signed_sim = rpc("simulateTransaction", [
        signed_b64,
        {
            "encoding": "base64",
            "sigVerify": True,
            "replaceRecentBlockhash": False,
            "commitment": "confirmed",
        },
    ])
    signed_value = (signed_sim or {}).get("value")
    if not isinstance(signed_value, dict) or signed_value.get("err") is not None:
        raise RuntimeError("Signed ATA simulation failed: " + str(signed_value)[:300])

    current_height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
    if not isinstance(current_height, int) or current_height > last_valid_height:
        raise RuntimeError("Transaction blockhash expired before submission")

    signature = rpc("sendTransaction", [
        signed_b64,
        {
            "encoding": "base64",
            "skipPreflight": False,
            "preflightCommitment": "confirmed",
            "maxRetries": 0,
        },
    ])
    if not isinstance(signature, str) or not signature:
        raise RuntimeError("Devnet did not return a transaction signature")
    expected_signature = str(signed.signatures[0])
    if signature != expected_signature:
        raise RuntimeError("RPC signature does not match the locally signed transaction")

    final_status = wait_finalized(signature, last_valid_height)
    created = read_account(ata, commitment="finalized")
    if created is None:
        raise RuntimeError("Finalized ATA is not visible on chain")
    if created["owner"] != str(TOKEN_2022_PROGRAM):
        raise RuntimeError("Created ATA is not owned by Token-2022")
    if len(created["data"]) != EXPECTED_ATA_SIZE:
        raise RuntimeError("Created ATA size does not match expected extension-aware size")
    if created["data"][:32] != bytes(MINT):
        raise RuntimeError("Created ATA mint field does not match configured mint")
    if created["data"][32:64] != bytes(payer_pubkey):
        raise RuntimeError("Created ATA owner field does not match ephemeral owner")
    if created["data"][108] != 1:
        raise RuntimeError("Created Token-2022 ATA is not initialized")
    token2022.validate_token_account_for_program(
        created,
        str(TOKEN_2022_PROGRAM),
        str(MINT),
        str(payer_pubkey),
        "Finalized Devnet Token-2022 ATA",
    )
    created_lamports = created.get("lamports")
    if (
        isinstance(created_lamports, bool)
        or not isinstance(created_lamports, int)
        or created_lamports < rent_lamports
    ):
        raise RuntimeError("Finalized ATA does not hold the required rent-exempt balance")

    final_balance = rpc("getBalance", [str(payer_pubkey), {"commitment": "finalized"}])
    remaining_lamports = (final_balance or {}).get("value")
    if not isinstance(remaining_lamports, int) or remaining_lamports < 0:
        raise RuntimeError("Could not verify final payer balance")

    print({
        "result": "PASS",
        "cluster": "devnet",
        "genesis_hash": DEVNET_GENESIS_HASH,
        "mint": str(MINT),
        "mint_owner": mint_account["owner"],
        "mint_data_size": len(mint_account["data"]),
        "payer_public_key": str(payer_pubkey),
        "payer_starting_balance_lamports": starting_balance,
        "payer_final_balance_lamports": remaining_lamports,
        "ata": str(ata),
        "ata_bump": bump,
        "ata_owner_program": created["owner"],
        "ata_data_size": len(created["data"]),
        "ata_initialized": True,
        "rent_lamports": rent_lamports,
        "unsigned_simulation": "PASS",
        "signed_simulation": "PASS",
        "transaction_signature": signature,
        "confirmation_status": final_status.get("confirmationStatus"),
        "live_trading": "LOCKED",
        "mainnet_access": "BLOCKED",
    })


if __name__ == "__main__":
    main()
