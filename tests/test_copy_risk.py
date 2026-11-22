import unittest
from unittest.mock import patch

from copy_risk import check_copy_buy, wallet_exposure


class CopyWalletRiskTests(unittest.TestCase):
    def setUp(self):
        self.positions = {
            "MintA": {
                "invested": 5.0,
                "metadata": {"copy_wallet": "WalletA"}
            },
            "MintB": {
                "invested": 7.0,
                "metadata": {"copy_wallet": "WalletB"}
            },
            "MintC": {
                "invested": 3.0,
                "metadata": {"copy_wallet": "WalletA"}
            },
            "ManualMint": {
                "invested": 4.0,
                "metadata": {"strategy": "main"}
            }
        }

    def test_exposure_isolated_by_wallet(self):
        state = wallet_exposure(self.positions, "WalletA")
        self.assertEqual(state["open_positions"], 2)
        self.assertEqual(state["exposure_usd"], 8.0)
        self.assertEqual(state["mints"], {"MintA", "MintC"})

    def test_buy_is_capped_by_remaining_wallet_budget(self):
        with patch.dict("os.environ", {
            "TROJAN_COPY_MAX_EXPOSURE_PER_WALLET_USD": "10",
            "TROJAN_COPY_MAX_TRADE_SIZE_USD": "5",
            "TROJAN_COPY_MAX_POSITIONS_PER_WALLET": "3",
            "TROJAN_MIN_TRADE_SIZE_USD": "1"
        }, clear=False):
            result = check_copy_buy(self.positions, "WalletA", "MintD", 5)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["trade_size"], 2.0)

    def test_existing_mint_cannot_be_mixed_between_wallets(self):
        result = check_copy_buy(self.positions, "WalletB", "MintA", 5)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "TOKEN_ALREADY_OPEN_UNDER_OTHER_OWNER")

    def test_wallet_position_limit_is_enforced(self):
        with patch.dict("os.environ", {
            "TROJAN_COPY_MAX_POSITIONS_PER_WALLET": "2"
        }, clear=False):
            result = check_copy_buy(self.positions, "WalletA", "MintD", 5)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_WALLET_POSITION_LIMIT")

    def test_wallet_daily_loss_limit_blocks_new_copy_entries(self):
        with patch.dict("os.environ", {
            "TROJAN_COPY_MAX_DAILY_LOSS_PER_WALLET_USD": "10"
        }, clear=False):
            result = check_copy_buy({}, "WalletA", "MintD", 5, daily_loss=10.25)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "COPY_WALLET_DAILY_LOSS_LIMIT")

    def test_unrelated_positions_do_not_consume_wallet_budget(self):
        with patch.dict("os.environ", {
            "TROJAN_COPY_MAX_POSITIONS_PER_WALLET": "3",
            "TROJAN_COPY_MAX_EXPOSURE_PER_WALLET_USD": "25"
        }, clear=False):
            result = check_copy_buy(self.positions, "WalletC", "MintD", 5)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["trade_size"], 5.0)


if __name__ == "__main__":
    unittest.main()
