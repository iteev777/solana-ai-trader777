import unittest
from unittest.mock import patch

import paper_trading


class PaperPositionInputHardeningTests(unittest.TestCase):
    def test_positive_finite_accepts_valid_numeric_inputs(self):
        self.assertEqual(paper_trading._positive_finite(1), 1.0)
        self.assertEqual(paper_trading._positive_finite("0.25"), 0.25)

    def test_positive_finite_rejects_nan_infinity_zero_and_negative_values(self):
        for value in (float("nan"), float("inf"), float("-inf"), 0, -1, None, "bad"):
            with self.subTest(value=value):
                self.assertIsNone(paper_trading._positive_finite(value))

    def test_open_position_rejects_nonfinite_price_before_database_access(self):
        with patch.object(paper_trading.database, "open_position_atomic") as open_atomic:
            for price in (float("nan"), float("inf"), float("-inf")):
                self.assertEqual(paper_trading.open_position("MintBad", price), "Invalid price")
            open_atomic.assert_not_called()

    def test_close_position_rejects_nonfinite_price_before_database_access(self):
        with patch.object(paper_trading.database, "close_position_atomic") as close_atomic:
            for price in (float("nan"), float("inf"), float("-inf")):
                self.assertEqual(paper_trading.close_position("MintBad", price), "Invalid price")
            close_atomic.assert_not_called()

    def test_update_position_rejects_nonfinite_mark_before_database_access(self):
        with patch.object(paper_trading, "refresh_account") as refresh:
            for price in (float("nan"), float("inf"), float("-inf")):
                self.assertEqual(paper_trading.update_position("MintBad", price), "Invalid price")
            refresh.assert_not_called()

    def test_bad_policy_configuration_does_not_mutate_position_state(self):
        original_positions = paper_trading.positions
        paper_trading.positions = {
            "MintPolicy": {
                "entry_price": 1.0,
                "metadata": {},
                "opened_at": "2026-01-01T00:00:00+00:00",
            }
        }
        try:
            with patch.object(paper_trading, "refresh_account"), \
                 patch.object(paper_trading, "get_dca_steps", side_effect=ValueError("bad JSON")), \
                 patch.object(paper_trading.database, "update_position_metadata") as update_meta:
                result = paper_trading.update_position("MintPolicy", 1.1)
            self.assertEqual(result, "Invalid position policy configuration")
            update_meta.assert_not_called()
        finally:
            paper_trading.positions = original_positions


if __name__ == "__main__":
    unittest.main()
