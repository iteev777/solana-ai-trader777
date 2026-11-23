import unittest

import main


class CandidateMetadataMergeTests(unittest.TestCase):
    def test_discovery_metadata_fills_missing_market_provider_fields(self):
        pair = {
            "name": "Unknown",
            "symbol": "Unknown",
            "info": {},
        }
        candidate = {
            "name": "Candidate Token",
            "symbol": "CANDY",
            "icon": "https://example.com/token.png",
            "website": "https://example.com",
            "websites": [{"url": "https://example.com/docs"}],
            "freezeAuthority": "Authority111",
            "mutable": False,
        }
        merged = main.merge_candidate_metadata(pair, candidate)
        self.assertEqual(merged["name"], "Candidate Token")
        self.assertEqual(merged["symbol"], "CANDY")
        self.assertEqual(merged["logoURI"], "https://example.com/token.png")
        self.assertEqual(merged["website"], "https://example.com")
        self.assertEqual(merged["websites"], candidate["websites"])
        self.assertEqual(merged["freezeAuthority"], "Authority111")
        self.assertIs(merged["mutable"], False)
        self.assertEqual(pair, {"name": "Unknown", "symbol": "Unknown", "info": {}})

    def test_real_market_metadata_is_not_overwritten_by_discovery_placeholders(self):
        pair = {
            "name": "Verified Pair Name",
            "symbol": "VERIFIED",
            "logoURI": "https://market.example/logo.png",
            "websites": [{"url": "https://market.example/top-level"}],
            "info": {"websites": [{"url": "https://market.example"}]},
        }
        candidate = {
            "name": "Unknown",
            "symbol": "Unknown",
            "icon": "https://candidate.example/icon.png",
            "website": "https://candidate.example",
            "websites": [{"url": "https://candidate.example/docs"}],
        }
        merged = main.merge_candidate_metadata(pair, candidate)
        self.assertEqual(merged["name"], "Verified Pair Name")
        self.assertEqual(merged["symbol"], "VERIFIED")
        self.assertEqual(merged["logoURI"], "https://market.example/logo.png")
        self.assertEqual(merged["websites"], pair["websites"])
        self.assertEqual(merged["info"]["websites"], pair["info"]["websites"])
        self.assertNotIn("website", merged)

    def test_nested_token_metadata_image_and_authority_are_supported(self):
        merged = main.merge_candidate_metadata({}, {
            "tokenMeta": {
                "image": "https://example.com/nested.png",
                "updateAuthority": "UpdateAuthority111",
            }
        })
        self.assertEqual(merged["logoURI"], "https://example.com/nested.png")
        self.assertEqual(merged["updateAuthority"], "UpdateAuthority111")


if __name__ == "__main__":
    unittest.main()
