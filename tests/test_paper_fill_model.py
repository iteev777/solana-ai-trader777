import math
import os
import unittest
from unittest.mock import patch

import paper_fill_model
import paper_trading


class PaperFillModelTests(unittest.TestCase):
    def test_default_slippage_worsens_buy_and_sell_marks(self):
        with patch.dict(os.environ, {"TROJAN_PAPER_SLIPPAGE_BPS": "25"}):
            buy, buy_bps = paper_fill_model.buy_fill_price(100.0)
            sell, sell_bps = paper_fill_model.sell_fill_price(100.0)
        self.assertAlmostEqual(buy, 100.25)
        self.assertAlmostEqual(sell, 99.75)
        self.assertEqual((buy_bps, sell_bps), (25, 25))

    def test_entry_uses_route_impact_when_greater_than_configured_slippage(self):
        metadata = {"entry_intelligence": {"quote": {"price_impact_percent": 1.2}}}
        with patch.dict(os.environ, {"TROJAN_PAPER_SLIPPAGE_BPS": "25"}):
            fill, bps = paper_fill_model.buy_fill_price(100.0, metadata)
        self.assertEqual(bps, 120)
        self.assertAlmostEqual(fill, 101.2)

    def test_slippage_configuration_is_bounded_and_invalid_values_fall_back(self):
        with patch.dict(os.environ, {"TROJAN_PAPER_SLIPPAGE_BPS": "900"}):
            self.assertEqual(paper_fill_model.configured_slippage_bps(), 300)
        with patch.dict(os.environ, {"TROJAN_PAPER_SLIPPAGE_BPS": "nan"}):
            self.assertEqual(
                paper_fill_model.configured_slippage_bps(),
                paper_fill_model.DEFAULT_SLIPPAGE_BPS,
            )

    def test_nonfinite_or_nonpositive_marks_are_rejected(self):
        for value in (0, -1, float("nan"), float("inf"), None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    paper_fill_model.buy_fill_price(value)
                with self.assertRaises(ValueError):
                    paper_fill_model.sell_fill_price(value)

    def test_persisted_exit_slippage_is_used_for_partial_and_full_closes(self):
        fill, bps = paper_fill_model.sell_fill_price(
            100.0, {"paper_exit_slippage_bps": 75}
        )
        self.assertEqual(bps, 75)
        self.assertAlmostEqual(fill, 99.25)


class PaperFillIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original_positions = paper_trading.positions
        self.original_balance = paper_trading.balance
        paper_trading.positions = {}
        paper_trading.balance = 100.0

    def tearDown(self):
        paper_trading.positions = self.original_positions
        paper_trading.balance = self.original_balance

    def test_open_position_books_route_impact_as_worse_entry_price(self):
        with (
            patch.object(paper_trading, "refresh_account"),
            patch("adaptive_learning.get_size_multiplier", return_value=1.0),
            patch.object(paper_trading.database, "open_position_atomic", return_value="POSITION OPENED") as opened,
            patch.dict(os.environ, {"TROJAN_PAPER_SLIPPAGE_BPS": "25"}),
        ):
            result = paper_trading.open_position(
                "MintEntry",
                100.0,
                metadata={"entry_intelligence": {"quote": {"price_impact_percent": 1.0}}},
                trade_size=5.0,
            )
        self.assertEqual(result, "PAPER BUY: MintEntry")
        kwargs = opened.call_args.kwargs
        self.assertAlmostEqual(kwargs["entry_price"], 101.0)
        self.assertEqual(kwargs["metadata"]["paper_entry_slippage_bps"], 100)

    def test_close_position_books_worse_sell_price(self):
        paper_trading.positions = {
            "MintExit": {"metadata": {"paper_exit_slippage_bps": 50}}
        }
        with (
            patch.object(paper_trading, "refresh_account"),
            patch.object(
                paper_trading.database,
                "close_position_atomic",
                return_value={"profit_loss": 1.0, "balance": 101.0},
            ) as closed,
        ):
            result = paper_trading.close_position("MintExit", 100.0)
        self.assertAlmostEqual(closed.call_args.kwargs["current_price"], 99.5)
        self.assertEqual(result["slippage_bps"], 50)
        self.assertEqual(result["market_price"], 100.0)

    def test_dca_books_worse_buy_price(self):
        paper_trading.positions = {
            "MintDca": {
                "entry_price": 100.0,
                "metadata": {},
                "opened_at": "2026-01-01T00:00:00+00:00",
            }
        }
        with (
            patch.object(paper_trading, "refresh_account"),
            patch.object(paper_trading, "get_dca_policy", return_value={
                "enabled": True, "steps": [{"drop_pct": 5, "add_usd": 2.5}],
                "measure_from": "average", "cooldown_minutes": 0,
                "min_hold_minutes": 0, "price_max_age_seconds": 30,
            }),
            patch.object(paper_trading.exit_policy, "get_exit_policy", return_value={"stop_loss_pct": 9}),
            patch.object(paper_trading, "get_exit_steps", return_value=[]),
            patch.object(paper_trading.database, "add_position_investment_atomic", return_value={"result": "DCA ADDED"}) as add,
            patch.object(paper_trading.database, "update_position_metadata"),
            patch.dict(os.environ, {"TROJAN_PAPER_SLIPPAGE_BPS": "25"}),
        ):
            result = paper_trading.update_position("MintDca", 94.0, price_timestamp=__import__("time").time())
        self.assertEqual(result["slippage_bps"], 25)
        self.assertAlmostEqual(add.call_args.args[2], 94.235)

    def test_partial_exit_books_worse_sell_price(self):
        paper_trading.positions = {
            "MintPartial": {
                "entry_price": 100.0,
                "metadata": {},
                "opened_at": "2026-01-01T00:00:00+00:00",
            }
        }
        with (
            patch.object(paper_trading, "refresh_account"),
            patch.object(paper_trading, "get_dca_policy", return_value={
                "enabled": False, "steps": [], "measure_from": "average",
                "cooldown_minutes": 60, "min_hold_minutes": 0,
                "price_max_age_seconds": 30,
            }),
            patch.object(paper_trading.exit_policy, "get_exit_policy", return_value={"stop_loss_pct": 9}),
            patch.object(paper_trading, "get_exit_steps", return_value=[{"profit_pct": 5, "sell_fraction": 0.5}]),
            patch.object(paper_trading.database, "update_position_metadata"),
            patch.object(
                paper_trading.database,
                "partial_close_position_atomic",
                return_value={"result": "PARTIAL", "profit_loss": 1.0},
            ) as partial,
            patch.dict(os.environ, {"TROJAN_PAPER_SLIPPAGE_BPS": "25"}),
        ):
            result = paper_trading.update_position("MintPartial", 110.0)
        self.assertAlmostEqual(partial.call_args.args[2], 109.725)
        self.assertEqual(result["slippage_bps"], 25)
        self.assertEqual(result["fill_model"], "CONSERVATIVE_MARKET_SLIPPAGE")


if __name__ == "__main__":
    unittest.main()
