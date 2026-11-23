"""Read-only Token-2022 Devnet E2E for the supplied 1% transfer-fee mint.

The runner creates a throwaway in-memory payer, funds it on Devnet, reads the
real mint over RPC, derives the Token-2022 ATA, and simulates an unsigned
CreateIdempotent instruction. It never signs, broadcasts, or touches mainnet.
"""
import os
import time

from solders.keypair import Keypair

DEVNET_URL = "https://api.devnet.solana.com"
DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
TOKEN_2022_MINT = "CFTBSCy68VqUzGeMwjmgzufeBgGi64i281f4iUxzavj7"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
LAMPORTS_PER_SOL = 1_000_000_000
AIRDROP_LAMPORTS = 100_000_000
TIMEOUT_SECONDS = 120


def rpc(method, params):
    import requests

    response = requests.post(
        DEVNET_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Malformed Devnet RPC response")
    if payload.get("error"):
        raise RuntimeError(method + " failed: " + str(payload["error"])[:240])
    return payload.get("result")


def require_devnet():
    if rpc("getGenesisHash", []) != DEVNET_GENESIS_HASH:
        raise RuntimeError("RPC endpoint is not Solana Devnet")


def fund_temporary_payer(address):
    signature = rpc("requestAirdrop", [address, AIRDROP_LAMPORTS])
    if not isinstance(signature, str) or not signature:
        raise RuntimeError("Devnet faucet did not return an airdrop signature")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = rpc("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        status = (result or {}).get("value", [None])[0]
        if status and status.get("err") is not None:
            raise RuntimeError("Devnet airdrop failed: " + str(status["err"])[:200])
        if status and status.get("confirmationStatus") in ("confirmed", "finalized"):
            balance = rpc("getBalance", [address, {"commitment": "confirmed"}])
            if (balance or {}).get("value", 0) >= AIRDROP_LAMPORTS:
                return signature, balance["value"]
        time.sleep(2)
    raise TimeoutError("Temporary Devnet payer was not funded in time")


def main():
    if os.getenv("TROJAN_DEVNET_TOKEN2022_E2E_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError("Set TROJAN_DEVNET_TOKEN2022_E2E_ENABLED=true")

    require_devnet()
    os.environ["SOLANA_RPC_URL"] = DEVNET_URL
    os.environ.pop("SOLANA_RPC_URLS", None)

    import solana_onchain as chain
    import token2022_inspector as token2022
    import ata_manager

    chain.RPC_URL = DEVNET_URL

    # Fetch and validate the actual on-chain mint, not a copied Explorer dump.
    mint_account = chain._account(TOKEN_2022_MINT)
    if mint_account["owner"] != TOKEN_2022_PROGRAM:
        raise RuntimeError("Mint owner is not Token-2022")
    raw = mint_account["data"]
    inspected = token2022.inspect_token2022_mint(TOKEN_2022_MINT)
    compatible = token2022.validate_swap_compatible_mint_data(raw)
    config = compatible["transfer_fee_config"]
    if inspected["decimals"] != 6 or config is None:
        raise RuntimeError("Mint decimals or TransferFeeConfig do not match expected test mint")

    epoch_info = rpc("getEpochInfo", [{"commitment": "confirmed"}])
    epoch = epoch_info.get("epoch")
    if not isinstance(epoch, int):
        raise RuntimeError("Devnet returned an invalid epoch")
    active_schedule = token2022.select_transfer_fee_schedule(config, epoch)
    if active_schedule["basis_points"] != 100:
        raise RuntimeError("Active transfer fee is not exactly 100 basis points")

    probe_amount = 100_000_000  # 100 raw-token units at 6 decimals.
    calculated_fee = token2022.transfer_fee_for_amount(active_schedule, probe_amount)
    expected_fee = min(
        active_schedule["maximum_fee"],
        (probe_amount * 100 + 9_999) // 10_000,
    )
    if calculated_fee != expected_fee or calculated_fee != 1_000_000:
        raise RuntimeError("Transfer-fee calculation did not produce the expected 1% result")

    # A fresh ephemeral payer avoids requesting the user's private key or
    # spending the user's SOL. The only on-chain write is a Devnet faucet airdrop.
    payer = Keypair()
    payer_address = str(payer.pubkey())
    airdrop_signature, payer_balance = fund_temporary_payer(payer_address)

    # Build and simulate the actual Token-2022 ATA instruction. No signature
    # verification, signing, broadcast, or account creation is performed.
    ata_order = ata_manager.create_ata_order(
        payer_address, TOKEN_2022_MINT, min_sol_reserve_lamports=0
    )
    if ata_order.get("status") != "UNSIGNED_ATA_ORDER_SIMULATED":
        raise RuntimeError("ATA manager did not return a simulated unsigned order")
    if ata_order.get("token_program") != TOKEN_2022_PROGRAM:
        raise RuntimeError("ATA order selected the wrong token program")
    if ata_order.get("token_account_size") != 182:
        raise RuntimeError("ATA size did not include TransferFeeAmount and ImmutableOwner")
    if ata_order.get("simulation", {}).get("status") != "PASS":
        raise RuntimeError("Unsigned Token-2022 ATA simulation did not pass")
    if ata_order.get("signing") != "EXTERNAL_WALLET_REQUIRED" or ata_order.get("broadcast") != "DISABLED":
        raise RuntimeError("ATA order unexpectedly enabled signing or broadcast")

    print({
        "result": "PASS",
        "cluster": "devnet",
        "genesis_hash": DEVNET_GENESIS_HASH,
        "mint": TOKEN_2022_MINT,
        "mint_owner": mint_account["owner"],
        "mint_account_size": len(raw),
        "decimals": inspected["decimals"],
        "extensions": [item["name"] for item in compatible["extensions"]],
        "current_epoch": epoch,
        "active_transfer_fee_basis_points": active_schedule["basis_points"],
        "active_transfer_fee_maximum_raw": active_schedule["maximum_fee"],
        "probe_amount_raw": probe_amount,
        "probe_fee_raw": calculated_fee,
        "temporary_payer": payer_address,
        "temporary_payer_balance_lamports": payer_balance,
        "airdrop_signature": airdrop_signature,
        "ata": ata_order["ata"],
        "ata_token_program": ata_order["token_program"],
        "ata_data_size": ata_order["token_account_size"],
        "ata_rent_lamports": ata_order["rent_lamports"],
        "ata_network_fee_lamports": ata_order["network_fee_lamports"],
        "unsigned_ata_simulation": "PASS",
        "signing": "NOT_PERFORMED",
        "broadcast": "DISABLED",
        "ata_created_on_chain": False,
        "live_trading": "LOCKED",
    })


if __name__ == "__main__":
    main()
