import unittest
from protocol_quotes import CpmmSnapshot, quote_raydium_cpmm_exact_in, validate_cpmm_snapshot


class RaydiumCpmmMathTests(unittest.TestCase):
    def snapshot(self, **overrides):
        values = dict(
            protocol="raydium_cpmm", pool_id="pool-1", mint_a="A", mint_b="B",
            reserve_a=1_000_000, reserve_b=2_000_000, trade_fee_rate=2500,
            slot=123, observed_at=1000.0,
        )
        values.update(overrides)
        return CpmmSnapshot(**values)

    def test_exact_input_quote_and_fee(self):
        quote = quote_raydium_cpmm_exact_in(
            self.snapshot(), "A", 1000, 100, now=1001.0
        )
        self.assertEqual(quote["fee_amount"], 3)
        self.assertEqual(quote["out_amount"], 1993)
        self.assertEqual(quote["minimum_out_amount"], 1973)
        self.assertEqual(quote["execution"], "DISABLED")

    def test_reverse_direction(self):
        quote = quote_raydium_cpmm_exact_in(
            self.snapshot(), "B", 2000, 50, now=1001.0
        )
        self.assertEqual(quote["output_mint"], "A")
        self.assertGreater(quote["out_amount"], 0)

    def test_rejects_stale_snapshot(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_cpmm_snapshot(self.snapshot(), now=1007.0)

    def test_rejects_transfer_fee_mint(self):
        with self.assertRaisesRegex(ValueError, "Token-2022"):
            quote_raydium_cpmm_exact_in(
                self.snapshot(transfer_fee_present=True), "A", 1000, now=1001.0
            )

    def test_rejects_unsupported_creator_fee_mode(self):
        with self.assertRaisesRegex(ValueError, "Creator fee mode"):
            validate_cpmm_snapshot(
                self.snapshot(creator_fee_rate=1000, creator_fee_on_input=False),
                now=1001.0,
            )

    def test_rejects_wrong_input_mint(self):
        with self.assertRaisesRegex(ValueError, "does not belong"):
            quote_raydium_cpmm_exact_in(
                self.snapshot(), "C", 1000, now=1001.0
            )


if __name__ == "__main__":
    unittest.main()
