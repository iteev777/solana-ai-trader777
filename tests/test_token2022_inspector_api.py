import base64
import os
import unittest
from unittest.mock import patch

import dashboard


class Token2022InspectionApiTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(b"trojan:secret").decode("ascii"),
            "X-Trojan-Action": "1",
            "Content-Type": "application/json",
        }

    def test_requires_action_header(self):
        headers = dict(self.headers)
        headers.pop("X-Trojan-Action")
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}):
            response = self.client.post("/api/token2022-inspect",
                                        json={"mint": "mint"}, headers=headers)
        self.assertEqual(response.status_code, 400)

    def test_returns_inspection_without_execution_authority(self):
        result = {
            "status": "INSPECTED_NOT_SWAP_APPROVED",
            "swap_eligibility": "BLOCKED_UNTIL_PROTOCOL_SPECIFIC_TOKEN_2022_SUPPORT",
            "execution_enabled": False,
        }
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("token2022_inspector.inspect_token2022_mint", return_value=result) as inspect:
            response = self.client.post("/api/token2022-inspect",
                                        json={"mint": "public-mint"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["execution_enabled"])
        inspect.assert_called_once_with("public-mint")

    def test_rejects_invalid_mint(self):
        with patch.dict(os.environ, {"TROJAN_DASHBOARD_TOKEN": "secret"}), \
             patch("token2022_inspector.inspect_token2022_mint",
                   side_effect=ValueError("invalid mint")):
            response = self.client.post("/api/token2022-inspect",
                                        json={"mint": "bad"}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid mint")


if __name__ == "__main__":
    unittest.main()
