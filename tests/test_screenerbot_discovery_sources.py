import unittest
from unittest.mock import patch

from discovery import screenerbot_sources as sources


VALID_MINT = "So11111111111111111111111111111111111111112"


class ScreenerBotDiscoverySourceTests(unittest.TestCase):
    def test_only_valid_solana_base58_mints_are_accepted(self):
        self.assertTrue(sources._is_solana_mint(VALID_MINT))
        self.assertFalse(sources._is_solana_mint("0x1234"))
        self.assertFalse(sources._is_solana_mint("not-a-mint"))
        self.assertFalse(sources._is_solana_mint(None))

    def test_rugcheck_merges_new_recent_trending_and_verified_feeds(self):
        responses = [
            [{"mint": VALID_MINT, "symbol": "SOLX", "name": "Sol X"}],
            [{"mint": VALID_MINT, "symbol": "SOLX"}],
            [{"mint": VALID_MINT}],
            [{"mint": VALID_MINT, "symbol": "SOLX"}],
        ]
        with patch.object(sources, "_safe_fetch", side_effect=responses):
            result = sources.scan_rugcheck()
        self.assertEqual(len(result), 1)
        self.assertEqual(
            set(result[0]["discovery_sources"]),
            {"rugcheck_new", "rugcheck_recent", "rugcheck_trending", "rugcheck_verified"},
        )

    def test_jupiter_merges_recent_organic_traded_and_trending(self):
        responses = [
            [{"id": VALID_MINT, "name": "Jupiter Token", "symbol": "JUPX"}],
            [{"id": VALID_MINT}],
            [{"id": VALID_MINT}],
            [{"id": VALID_MINT}],
        ]
        with patch.object(sources, "_safe_fetch", side_effect=responses):
            result = sources.scan_jupiter()
        self.assertEqual(len(result), 1)
        self.assertEqual(
            set(result[0]["discovery_sources"]),
            {"jupiter_recent", "jupiter_organic", "jupiter_traded", "jupiter_trending"},
        )

    def test_coingecko_uses_solana_platform_contract_only(self):
        payload = [
            {"id": "valid", "name": "Valid", "symbol": "VAL", "platforms": {"solana": VALID_MINT}},
            {"id": "wrong", "name": "Wrong", "symbol": "BAD", "platforms": {"ethereum": "0x1234"}},
        ]
        with patch.object(sources, "_safe_fetch", return_value=payload):
            result = sources.scan_coingecko()
        self.assertEqual([item["tokenAddress"] for item in result], [VALID_MINT])

    def test_defillama_requires_solana_chain_and_valid_contract(self):
        payload = [
            {"name": "Solana Protocol", "symbol": "SP", "chains": ["Solana"], "address": VALID_MINT},
            {"name": "Ethereum Protocol", "symbol": "EP", "chains": ["Ethereum"], "address": VALID_MINT},
            {"name": "Invalid Address", "symbol": "IA", "chains": ["Solana"], "address": "0x1234"},
        ]
        with patch.object(sources, "_safe_fetch", return_value=payload):
            result = sources.scan_defillama()
        self.assertEqual([item["tokenAddress"] for item in result], [VALID_MINT])


if __name__ == "__main__":
    unittest.main()
