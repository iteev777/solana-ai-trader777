import unittest

from solana_onchain import PROGRAMS
from transaction_deltas import (
    DeltaKind, NATIVE_SOL_SENTINEL, SubjectAssetDelta, extract_subject_deltas
)


WALLET = "Wallet111111111111111111111111111111111111"
OTHER = "Other1111111111111111111111111111111111111"
MINT = "TokenMint111111111111111111111111111111111"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def balance(mint, owner, raw, decimals, index):
    return {
        "mint": mint, "owner": owner, "accountIndex": index,
        "uiTokenAmount": {"amount": str(raw), "decimals": decimals},
    }


def transaction(subject_is_fee_payer=True, native_delta=-1_000_000_000):
    wallet_index = 0 if subject_is_fee_payer else 1
    account_keys = [{"pubkey": WALLET if subject_is_fee_payer else OTHER}]
    if not subject_is_fee_payer:
        account_keys.append({"pubkey": WALLET})
    account_keys.extend([
        {"pubkey": "TokenAccountUSDC"},
        {"pubkey": "TokenAccountMint"},
    ])
    pre_sol = [10_000_000_000] * len(account_keys)
    post_sol = list(pre_sol)
    post_sol[wallet_index] += native_delta
    pre_rows = [balance(USDC, WALLET, 1_000_000, 6, 2 if not subject_is_fee_payer else 1)]
    post_rows = [
        balance(USDC, WALLET, 900_000, 6, 2 if not subject_is_fee_payer else 1),
        balance(MINT, WALLET, 500, 6, 3 if not subject_is_fee_payer else 2),
    ]
    return {
        "slot": 123, "blockTime": 1_700_000_000, "transactionIndex": 4,
        "signature": "signature-1",
        "transaction": {"message": {
            "accountKeys": account_keys,
            "instructions": [{"programId": PROGRAMS["raydium_cpmm"]}],
        }},
        "meta": {
            "err": None, "fee": 5_000,
            "preBalances": pre_sol, "postBalances": post_sol,
            "preTokenBalances": pre_rows, "postTokenBalances": post_rows,
            "loadedAddresses": {"writable": [], "readonly": []},
        },
    }


class TransactionDeltaTests(unittest.TestCase):
    def test_extracts_all_raw_token_and_native_asset_deltas(self):
        tx = transaction()
        rows = extract_subject_deltas(WALLET, tx)
        by_mint = {row.mint: row for row in rows}
        self.assertEqual(set(by_mint), {USDC, MINT, NATIVE_SOL_SENTINEL})
        self.assertEqual(by_mint[MINT].delta_raw, 500)
        self.assertEqual(by_mint[USDC].delta_raw, -100_000)
        self.assertEqual(by_mint[NATIVE_SOL_SENTINEL].delta_raw, -999_995_000)
        self.assertEqual(by_mint[MINT].kind, DeltaKind.TRADE)
        self.assertEqual(by_mint[MINT].venue, "raydium_cpmm")
        self.assertEqual(by_mint[MINT].signature, "signature-1")
        self.assertEqual(by_mint[MINT].slot, 123)

    def test_fee_metadata_is_preserved_without_charging_non_fee_payer_subject(self):
        tx = transaction(subject_is_fee_payer=False, native_delta=0)
        rows = extract_subject_deltas(WALLET, tx)
        by_mint = {row.mint: row for row in rows}
        self.assertEqual(by_mint[USDC].fee_native_raw, 0)
        self.assertEqual(by_mint[MINT].fee_native_raw, 0)
        self.assertNotIn(NATIVE_SOL_SENTINEL, by_mint)

    def test_native_delta_corrects_subject_owned_ata_rent(self):
        tx = transaction(native_delta=-1_002_005_000)
        tx["meta"]["postBalances"][2] += 2_000_000
        rows = extract_subject_deltas(WALLET, tx)
        native = next(row for row in rows if row.mint == NATIVE_SOL_SENTINEL)
        self.assertEqual(native.delta_raw, -1_000_000_000)

    def test_missing_or_ambiguous_subject_returns_no_deltas(self):
        tx = transaction()
        self.assertEqual(extract_subject_deltas("missing-wallet", tx), [])
        tx["transaction"]["message"]["accountKeys"].append({"pubkey": WALLET})
        self.assertEqual(extract_subject_deltas(WALLET, tx), [])

    def test_failed_transaction_returns_no_deltas(self):
        tx = transaction()
        tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
        self.assertEqual(extract_subject_deltas(WALLET, tx), [])

    def test_invalid_raw_balance_account_index_fails_closed(self):
        tx = transaction()
        tx["meta"]["postTokenBalances"][0]["accountIndex"] = 0
        self.assertEqual(extract_subject_deltas(WALLET, tx), [])

    def test_asset_delta_serialization_uses_string_kind(self):
        row = SubjectAssetDelta(
            chain="solana", wallet_address=WALLET, signature="sig", mint=MINT,
            slot=None, block_time=None, tx_index=0, delta_raw=1,
            before_raw=0, after_raw=1, decimals=6, kind=DeltaKind.TRANSFER,
            venue=None, fee_native_raw=0, success=True,
        )
        self.assertEqual(row.as_dict()["kind"], "transfer")

    def test_unknown_delta_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            DeltaKind.parse("unknown")


if __name__ == "__main__":
    unittest.main()
