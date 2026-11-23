"""Real Solana devnet E2E: faucet -> unsigned simulation -> approval ->
external client-side signature -> signed simulation -> broadcast -> finality.

Uses a throwaway in-memory Keypair and a 1-lamport SystemProgram transfer.
It never reads a wallet secret, never targets mainnet, and never unlocks the
native live broadcast path.
"""
import base64
import os
import time
from urllib.parse import urlparse

import requests
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

DEVNET_URL = os.getenv("SOLANA_DEVNET_RPC_URL", "https://api.devnet.solana.com").strip()
DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
LAMPORTS_PER_SOL = 1_000_000_000
TEST_TRANSFER_LAMPORTS = 1
REQUEST_TIMEOUT = 20
FINALITY_TIMEOUT = 60


def rpc(method, params):
    response = requests.post(
        DEVNET_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Devnet RPC returned a malformed response")
    if payload.get("error"):
        raise RuntimeError(method + " failed: " + str(payload["error"])[:240])
    return payload.get("result")


def require_devnet_endpoint():
    parsed = urlparse(DEVNET_URL)
    if parsed.scheme != "https" or parsed.hostname != "api.devnet.solana.com":
        raise RuntimeError("E2E is restricted to https://api.devnet.solana.com")
    genesis = rpc("getGenesisHash", [])
    if genesis != DEVNET_GENESIS_HASH:
        raise RuntimeError("RPC genesis hash does not match Solana devnet")


def wait_for_signature(signature, last_valid_block_height):
    deadline = time.monotonic() + FINALITY_TIMEOUT
    while time.monotonic() < deadline:
        status_result = rpc("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        values = (status_result or {}).get("value")
        if not isinstance(values, list) or len(values) != 1:
            raise RuntimeError("Devnet returned malformed signature status")
        status = values[0]
        if status:
            if status.get("err") is not None:
                raise RuntimeError("Devnet transaction failed: " + str(status["err"])[:240])
            if status.get("confirmationStatus") == "finalized":
                return status
        height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
        if isinstance(height, int) and height > last_valid_block_height:
            raise RuntimeError("Transaction blockhash expired before finality")
        time.sleep(2)
    raise TimeoutError("Devnet transaction did not reach finalized status in time")


def main():
    if os.getenv("TROJAN_DEVNET_E2E_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        raise RuntimeError("Set TROJAN_DEVNET_E2E_ENABLED=true to run the devnet-only harness")
    require_devnet_endpoint()

    # Ensure the shared chain module can only use this exact devnet endpoint.
    os.environ["SOLANA_RPC_URL"] = DEVNET_URL
    os.environ.pop("SOLANA_RPC_URLS", None)

    import native_execution
    import solana_onchain as chain

    chain.RPC_URL = DEVNET_URL
    payer = Keypair()
    recipient = Keypair()
    payer_address = str(payer.pubkey())
    recipient_address = str(recipient.pubkey())

    airdrop_signature = rpc("requestAirdrop", [payer_address, 20_000_000])
    if not isinstance(airdrop_signature, str) or not airdrop_signature:
        raise RuntimeError("Devnet faucet returned no airdrop signature")
    airdrop_deadline = time.monotonic() + 60
    while time.monotonic() < airdrop_deadline:
        result = rpc("getSignatureStatuses", [[airdrop_signature], {"searchTransactionHistory": True}])
        status = (result or {}).get("value", [None])[0]
        if status and status.get("err") is not None:
            raise RuntimeError("Devnet faucet airdrop failed: " + str(status["err"]))
        if status and status.get("confirmationStatus") in ("confirmed", "finalized"):
            break
        time.sleep(2)
    else:
        raise TimeoutError("Devnet faucet airdrop was not confirmed")

    latest = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    latest_value = (latest or {}).get("value") or {}
    blockhash_text = latest_value.get("blockhash")
    last_valid_height = latest_value.get("lastValidBlockHeight")
    if not blockhash_text or not isinstance(last_valid_height, int):
        raise RuntimeError("Devnet returned an invalid recent blockhash")

    instruction = transfer(TransferParams(
        from_pubkey=payer.pubkey(),
        to_pubkey=recipient.pubkey(),
        lamports=TEST_TRANSFER_LAMPORTS,
    ))
    blockhash = Hash.from_string(blockhash_text)
    message = Message.new_with_blockhash([instruction], payer.pubkey(), blockhash)
    unsigned_tx = Transaction.new_unsigned(message)
    unsigned_bytes = bytes(unsigned_tx)
    unsigned_b64 = base64.b64encode(unsigned_bytes).decode("ascii")

    # Stage 1: unsigned simulation. The only instruction is a 1-lamport
    # SystemProgram transfer between two ephemeral devnet accounts.
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
        raise RuntimeError("Unsigned devnet simulation failed: " + str(unsigned_value)[:240])

    os.environ["TROJAN_DEVNET_E2E_ENABLED"] = "true"
    approval = native_execution.register_native_approval(
        unsigned_b64,
        "devnet_system_transfer",
        last_valid_height,
        {
            "status": "PASS",
            "signature_verification": "DISABLED_UNSIGNED_SIMULATION",
        },
    )

    # Stage 2: wallet-side signing. The throwaway Keypair is the external
    # signer for this harness; no key material is sent to the bot or logged.
    signed_tx = Transaction.new_unsigned(message)
    signed_tx.sign([payer], blockhash)
    signed_bytes = bytes(signed_tx)
    signed_b64 = base64.b64encode(signed_bytes).decode("ascii")
    consumed = native_execution.consume_native_approval(
        signed_b64, approval["request_id"]
    )
    if consumed["protocol"] != "devnet_system_transfer":
        raise RuntimeError("Approval protocol does not match the devnet harness")

    # Stage 3: signed simulation with signature verification enabled.
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
        raise RuntimeError("Signed devnet simulation failed: " + str(signed_value)[:240])

    current_height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
    if not isinstance(current_height, int) or current_height > last_valid_height:
        raise RuntimeError("Devnet transaction blockhash expired before broadcast")

    # Stage 4: submit the externally signed bytes. This is a devnet-only test
    # path; execute_native_signed_transaction remains hard-locked.
    expected_signature = chain._b58encode(consumed["signature"])
    returned_signature = rpc("sendTransaction", [
        signed_b64,
        {
            "encoding": "base64",
            "skipPreflight": False,
            "preflightCommitment": "confirmed",
            "maxRetries": 0,
        },
    ])
    if returned_signature != expected_signature:
        raise RuntimeError("RPC signature does not match the externally signed transaction")

    # Stage 5: wait for finalized status and verify recipient balance delta.
    final_status = wait_for_signature(returned_signature, last_valid_height)
    recipient_balance = rpc("getBalance", [recipient_address, {"commitment": "finalized"}])
    if not isinstance(recipient_balance, dict) or recipient_balance.get("value") != TEST_TRANSFER_LAMPORTS:
        raise RuntimeError("Finalized recipient balance does not match the 1-lamport test transfer")

    print({
        "result": "PASS",
        "cluster": "devnet",
        "genesis_hash": DEVNET_GENESIS_HASH,
        "payer": payer_address,
        "recipient": recipient_address,
        "airdrop_signature": airdrop_signature,
        "transaction_signature": returned_signature,
        "confirmation_status": final_status.get("confirmationStatus"),
        "recipient_balance_lamports": recipient_balance["value"],
        "unsigned_simulation": "PASS",
        "signed_simulation": "PASS",
        "approval_message_sha256": consumed["message_sha256"],
        "live_broadcast_hard_lock": native_execution.NATIVE_BROADCAST_HARD_LOCK,
        "server_signing": False,
    })


if __name__ == "__main__":
    main()
