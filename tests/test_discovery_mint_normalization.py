import unittest

from discovery.token_aggregator import normalize_token


class DiscoveryMintNormalizationTests(unittest.TestCase):
    def test_accepts_valid_solana_mint(self):
        token = {
            "tokenAddress": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            "chainId": "solana",
        }
        self.assertEqual(
            normalize_token(token),
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        )

    def test_rejects_wrong_chain(self):
        self.assertIsNone(normalize_token({
            "tokenAddress": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            "chainId": "ethereum",
        }))

    def test_rejects_non_base58_and_malformed_addresses(self):
        for address in ("not-a-mint", "0x1234", "So1111111111111111111111111111111111111110", ""):
            with self.subTest(address=address):
                self.assertIsNone(normalize_token({
                    "tokenAddress": address,
                    "chainId": "solana",
                }))

    def test_excludes_native_sol_and_stablecoin_quote_assets(self):
        for address in (
            "So11111111111111111111111111111111111111112",
            "11111111111111111111111111111111",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        ):
            with self.subTest(address=address):
                self.assertIsNone(normalize_token({
                    "tokenAddress": address,
                    "chainId": "solana",
                }))

    def test_rejects_non_dict_and_missing_fields(self):
        self.assertIsNone(normalize_token(None))
        self.assertIsNone(normalize_token({}))
        self.assertIsNone(normalize_token({
            "tokenAddress": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        }))


if __name__ == "__main__":
    unittest.main()
