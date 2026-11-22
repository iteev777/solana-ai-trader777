import unittest

from copy_event_parser import parse_wallet_transaction
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
    instructions = []
    if program:
        instructions.append({"programId": program, "accounts": []})
    return {
        "blockTime": 123,
        "transaction": {"message": {
            "accountKeys": [{"pubkey": WALLET, "signer": True}] ,
            "instructions": instructions
        }},
        "meta": {
            "err": None, "fee": fee,
            "preBalances": [pre_sol], "postBalances": [post_sol],
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

    def test_unknown_if_swap_program_has_no_opposing_quote_flow(self):
        tx = transaction([], [balance(MINT, WALLET, 1_000_000, 6)], PROGRAMS["orca_whirlpool"])
        event = parse_wallet_transaction(tx, WALLET)
        self.assertEqual(event["action"], "TOKEN TRANSFER / UNKNOWN")
        self.assertEqual(event["classification_reason"], "SWAP_PROGRAM_WITH_AMBIGUOUS_BALANCE_FLOW")


if __name__ == "__main__":
    unittest.main()
