import unittest

from copy_event_parser import SOL_MINT, parse_wallet_transaction
from solana_onchain import PROGRAMS

WALLET = "Wallet111111111111111111111111111111111111"
MINT = "TokenMint111111111111111111111111111111111"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def balance(mint, owner, raw, decimals):
    return {
        "mint": mint, "owner": owner,
        "uiTokenAmount": {"amount": str(raw), "decimals": decimals}
    }


def transaction(pre, post, program=None, pre_sol=10_000_000_000, post_sol=10_000_000_000, fee=5000):
    pre = [dict(item) for item in pre]
    post = [dict(item) for item in post]
    max_accounts = {}
    for rows in (pre, post):
        counts = {}
        for item in rows:
            mint = item["mint"]
            counts[mint] = counts.get(mint, 0) + 1
        for mint, count in counts.items():
            max_accounts[mint] = max(max_accounts.get(mint, 0), count)
    account_bases = {}
    next_index = 1
    for mint, count in max_accounts.items():
        account_bases[mint] = next_index
        next_index += count

    def assign_indices(rows):
        counts = {}
        result = []
        for item in rows:
            mint = item["mint"]
            occurrence = counts.get(mint, 0)
            counts[mint] = occurrence + 1
            result.append(dict(item, accountIndex=account_bases[mint] + occurrence))
        return result

    pre = assign_indices(pre)
    post = assign_indices(post)
    account_keys = [{"pubkey": WALLET, "signer": True}] + [
        {"pubkey": "TokenAccount" + str(index)} for index in range(1, next_index)
    ]
    pre_balances = [pre_sol] + [0] * (next_index - 1)
    post_balances = [post_sol] + [0] * (next_index - 1)
    instructions = []
    if program:
        instructions.append({"programId": program, "accounts": []})
    return {
        "blockTime": 123,
        "transaction": {"message": {
            "accountKeys": account_keys,
            "instructions": instructions
        }},
        "meta": {
            "err": None, "fee": fee,
            "preBalances": pre_balances, "postBalances": post_balances,
            "preTokenBalances": pre, "postTokenBalances": post,
            "logMessages": ["swap"]
        }
    }


class CopyEventParserTests(unittest.TestCase):
    def test_classifies_buy_from_exact_wallet_token_and_quote_deltas(self):
        tx = transaction(
            [balance(USDC, WALLET, 20_000_000, 6)],
            [balance(USDC, WALLET, 10_000_000, 6), balance(MINT, WALLET, 250_000, 3)],
            PROGRAMS["raydium_cpmm"]
        )
        event = parse_wallet_transaction(tx, WALLET, signature="sig")
        self.assertEqual(event["action"], "BUY")
        self.assertEqual(event["classification_reason"], "TOKEN_IN_QUOTE_OUT")
        self.assertEqual(event["token_delta"], 250.0)
        self.assertEqual(event["quote_delta"], -10.0)

    def test_classifies_sell_from_opposing_wallet_deltas(self):
        tx = transaction(
            [balance(MINT, WALLET, 900_000, 3), balance(USDC, WALLET, 5_000_000, 6)],
            [balance(MINT, WALLET, 400_000, 3), balance(USDC, WALLET, 8_000_000, 6)],
            PROGRAMS["raydium_amm_v4"]
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "SELL")
        self.assertEqual(event["token_delta"], -500.0)
        self.assertEqual(event["quote_delta"], 3.0)

    def test_native_sol_flow_has_fee_removed(self):
        tx = transaction(
            [], [balance(MINT, WALLET, 1_000_000, 6)],
            PROGRAMS["pumpswap"], pre_sol=10_000_000_000,
            post_sol=9_000_000_000, fee=5000
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "BUY")
        self.assertAlmostEqual(event["economic_sol_delta"], -0.999995)


    def test_native_sol_quote_corrects_rent_in_subject_owned_token_accounts(self):
        tx = transaction(
            [], [balance(MINT, WALLET, 1_000_000, 6)],
            PROGRAMS["pumpswap"], pre_sol=10_000_000_000,
            post_sol=8_997_995_000, fee=5000
        )
        tx["meta"]["postBalances"][1] = 2_000_000  # 0.002 SOL ATA rent
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "BUY")
        self.assertAlmostEqual(event["economic_sol_delta"], -1.0)

    def test_wrapped_sol_account_is_not_counted_as_ata_rent(self):
        tx = transaction(
            [balance(SOL_MINT, WALLET, 0, 9)],
            [balance(SOL_MINT, WALLET, 1_000_000_000, 9),
             balance(MINT, WALLET, 1_000_000, 6)],
            PROGRAMS["pumpswap"], pre_sol=10_000_000_000,
            post_sol=9_000_000_000, fee=5000
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "BUY")
        self.assertAlmostEqual(event["economic_sol_delta"], -0.999995)

    def test_non_swap_token_transfer_remains_unknown_even_with_sol_fee(self):
        tx = transaction(
            [], [balance(MINT, WALLET, 1_000_000, 6)],
            None, pre_sol=10_000_000_000, post_sol=9_999_995_000, fee=5000
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "TOKEN TRANSFER / UNKNOWN")
        self.assertEqual(event["classification_reason"], "NO_RECOGNISED_SWAP_PROGRAM")

    def test_multiple_owned_accounts_are_aggregated_without_float_rounding(self):
        tx = transaction(
            [balance(USDC, WALLET, 50_000_000, 6)],
            [balance(USDC, WALLET, 49_000_000, 6),
             balance(MINT, WALLET, 1, 9),
             balance(MINT, WALLET, 2, 9)],
            PROGRAMS["fluxbeam_amm"]
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "BUY")
        self.assertAlmostEqual(event["token_delta"], 0.000000003)

    def test_rejects_failed_or_unrelated_transactions(self):
        tx = transaction([], [balance(MINT, WALLET, 1, 0)], PROGRAMS["pumpswap"])
        tx["meta"]["err"] = {"InstructionError": [0, "failed"]}
        self.assertIsNone(parse_wallet_transaction(tx, WALLET))
        tx["meta"]["err"] = None
        self.assertIsNone(parse_wallet_transaction(tx, "OtherWallet"))


    def test_non_fee_payer_wallet_can_be_classified_from_its_own_swap_deltas(self):
        tx = transaction(
            [balance(USDC, WALLET, 10_000_000, 6)],
            [balance(USDC, WALLET, 9_000_000, 6), balance(MINT, WALLET, 1_000_000, 6)],
            PROGRAMS["raydium_cpmm"], pre_sol=10_000_000_000,
            post_sol=10_000_000_000, fee=5000
        )
        tx["transaction"]["message"]["accountKeys"].insert(
            0, {"pubkey": "OtherFeePayer1111111111111111111111111111", "signer": True}
        )
        tx["meta"]["preBalances"].insert(0, 5_000_000_000)
        tx["meta"]["postBalances"].insert(0, 5_000_000_000)
        for rows in (tx["meta"]["preTokenBalances"], tx["meta"]["postTokenBalances"]):
            for item in rows:
                item["accountIndex"] += 1
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "BUY")
        self.assertEqual(event["classification_reason"], "TOKEN_IN_QUOTE_OUT")


    def test_mixed_quote_asset_directions_remain_unknown(self):
        usdt = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
        tx = transaction(
            [balance(USDC, WALLET, 10_000_000, 6)],
            [balance(USDC, WALLET, 9_000_000, 6),
             balance(usdt, WALLET, 2_000_000, 6),
             balance(MINT, WALLET, 1_000_000, 6)],
            PROGRAMS["raydium_cpmm"]
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "TOKEN TRANSFER / UNKNOWN")
        self.assertEqual(event["classification_reason"], "MULTIPLE_QUOTE_LEGS")

    def test_unknown_if_swap_program_has_no_opposing_quote_flow(self):
        tx = transaction([], [balance(MINT, WALLET, 1_000_000, 6)], PROGRAMS["orca_whirlpool"])
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "TOKEN TRANSFER / UNKNOWN")
        self.assertEqual(event["classification_reason"], "SWAP_PROGRAM_WITH_AMBIGUOUS_BALANCE_FLOW")


    def test_mixed_stable_and_material_native_quote_remains_unknown(self):
        tx = transaction(
            [balance(USDC, WALLET, 10_000_000, 6)],
            [balance(USDC, WALLET, 9_000_000, 6), balance(MINT, WALLET, 1_000_000, 6)],
            PROGRAMS["raydium_cpmm"], pre_sol=10_000_000_000,
            post_sol=9_000_000_000
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "TOKEN TRANSFER / UNKNOWN")
        self.assertEqual(event["classification_reason"], "MIXED_NATIVE_AND_STABLE_QUOTE")

    def test_multiple_non_quote_token_legs_remain_unknown(self):
        second_mint = "SecondMint111111111111111111111111111111"
        tx = transaction(
            [balance(USDC, WALLET, 10_000_000, 6)],
            [balance(USDC, WALLET, 9_000_000, 6),
             balance(MINT, WALLET, 1_000_000, 6),
             balance(second_mint, WALLET, 2_000_000, 6)],
            PROGRAMS["raydium_cpmm"]
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "TOKEN TRANSFER / UNKNOWN")
        self.assertEqual(event["classification_reason"], "MULTIPLE_TOKEN_LEGS")

    def test_multiple_stable_quote_legs_remain_unknown(self):
        usdt = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
        tx = transaction(
            [balance(USDC, WALLET, 10_000_000, 6), balance(usdt, WALLET, 10_000_000, 6)],
            [balance(USDC, WALLET, 9_000_000, 6), balance(usdt, WALLET, 9_000_000, 6),
             balance(MINT, WALLET, 1_000_000, 6)],
            PROGRAMS["raydium_cpmm"]
        )
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "TOKEN TRANSFER / UNKNOWN")
        self.assertEqual(event["classification_reason"], "MULTIPLE_QUOTE_LEGS")

    def test_missing_token_balance_metadata_fails_closed(self):
        tx = transaction([], [balance(MINT, WALLET, 1_000_000, 6)], PROGRAMS["raydium_cpmm"])
        tx["meta"]["preTokenBalances"] = None
        self.assertIsNone(parse_wallet_transaction(tx, WALLET))

    def test_duplicate_subject_account_key_fails_closed(self):
        tx = transaction([], [balance(MINT, WALLET, 1_000_000, 6)], PROGRAMS["raydium_cpmm"])
        tx["transaction"]["message"]["accountKeys"].append({"pubkey": WALLET})
        self.assertIsNone(parse_wallet_transaction(tx, WALLET))

    def test_loaded_address_program_index_is_resolved(self):
        tx = transaction(
            [balance(USDC, WALLET, 10_000_000, 6)],
            [balance(USDC, WALLET, 9_000_000, 6), balance(MINT, WALLET, 1_000_000, 6)]
        )
        tx["transaction"]["message"]["instructions"] = [{"programIdIndex": len(tx["transaction"]["message"]["accountKeys"])}]
        tx["meta"]["loadedAddresses"] = {"writable": [], "readonly": [PROGRAMS["raydium_cpmm"]]}
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "BUY")


if __name__ == "__main__":
    unittest.main()
