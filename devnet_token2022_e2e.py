"""Read-only Token-2022 Devnet E2E for the supplied 1% transfer-fee mint.

Uses the user's explicitly supplied public Devnet wallet only as the simulated
ATA payer. It never reads a secret, signs, broadcasts, or touches mainnet.
"""
import os

DEVNET_URL = "https://api.devnet.solana.com"
DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
TOKEN_2022_MINT = "CFTBSCy68VqUzGeMwjmgzufeBgGi64i281f4iUxzavj7"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TEST_WALLET = "DrXL17U7RS2Nx6KVTUStpqhcsmDod3RfMS7RnTTSgTRH"


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


def main():
    if os.getenv("TROJAN_DEVNET_TOKEN2022_E2E_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError("Set TROJAN_DEVNET_TOKEN2022_E2E_ENABLED=true")

    if rpc("getGenesisHash", []) != DEVNET_GENESIS_HASH:
        raise RuntimeError("RPC endpoint is not Solana Devnet")

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

    wallet_balance_result = rpc("getBalance", [TEST_WALLET, {"commitment": "confirmed"}])
    wallet_balance = (wallet_balance_result or {}).get("value")
    if not isinstance(wallet_balance, int) or wallet_balance <= 0:
        raise RuntimeError("Supplied Devnet wallet has no spendable SOL balance for ATA simulation")

    # Build and simulate the actual Token-2022 ATA instruction. This is an
    # unsigned, sigVerify=false simulation: it cannot spend SOL or create ATA.
    ata_order = ata_manager.create_ata_order(
        TEST_WALLET, TOKEN_2022_MINT, min_sol_reserve_lamports=0
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
        "wallet": TEST_WALLET,
        "wallet_balance_lamports": wallet_balance,
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
