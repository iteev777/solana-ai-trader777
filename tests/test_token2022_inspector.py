import base64
import unittest
from unittest.mock import patch

import solana_onchain as chain
import token2022_inspector as inspector


def key(byte):
    return chain._b58encode(bytes([byte]) * 32)


def mint_data(extensions=b""):
    raw = bytearray(166)
    raw[44] = 6
    raw[45] = 1
    raw[165] = inspector.MINT_ACCOUNT_TYPE
    return bytes(raw) + extensions


def tlv(extension_type, value=b""):
    return (extension_type.to_bytes(2, "little")
            + len(value).to_bytes(2, "little") + value)


class Token2022InspectorTests(unittest.TestCase):
    def test_decodes_both_epoch_transfer_fee_schedules(self):
        config = bytearray(108)
        config[72:80] = (10).to_bytes(8, "little")
        config[80:88] = (500).to_bytes(8, "little")
        config[88:90] = (100).to_bytes(2, "little")
        config[90:98] = (20).to_bytes(8, "little")
        config[98:106] = (900).to_bytes(8, "little")
        config[106:108] = (250).to_bytes(2, "little")
        result = inspector.decode_transfer_fee_config(
            mint_data(tlv(1, bytes(config)))
        )
        self.assertEqual(result["older"], {
            "epoch": 10, "maximum_fee": 500, "basis_points": 100,
        })
        self.assertEqual(result["newer"], {
            "epoch": 20, "maximum_fee": 900, "basis_points": 250,
        })
        self.assertTrue(result["epoch_selection_required"])

    def test_selects_transfer_fee_schedule_at_activation_epoch(self):
        config = {
            "older": {"epoch": 10, "basis_points": 100, "maximum_fee": 500},
            "newer": {"epoch": 20, "basis_points": 250, "maximum_fee": 900},
        }
        self.assertEqual(
            inspector.select_transfer_fee_schedule(config, 19), config["older"]
        )
        self.assertEqual(
            inspector.select_transfer_fee_schedule(config, 20), config["newer"]
        )

    def test_epoch_schedule_selection_rejects_invalid_epoch(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            inspector.select_transfer_fee_schedule(
                {"older": {}, "newer": {"epoch": 2}}, True
            )

    def test_transfer_fee_rounds_up_and_respects_maximum(self):
        schedule = {"basis_points": 100, "maximum_fee": 50}
        self.assertEqual(inspector.transfer_fee_for_amount(schedule, 1), 1)
        self.assertEqual(inspector.transfer_fee_for_amount(schedule, 10_000), 50)
        self.assertEqual(
            inspector.transfer_fee_for_amount(
                {"basis_points": 100, "maximum_fee": 1_000}, 10_000
            ), 100
        )

    def test_transfer_fee_rejects_invalid_schedule_and_boolean_amount(self):
        with self.assertRaisesRegex(ValueError, "basis points"):
            inspector.transfer_fee_for_amount(
                {"basis_points": 10_001, "maximum_fee": 100}, 1_000
            )
        with self.assertRaisesRegex(ValueError, "non-negative"):
            inspector.transfer_fee_for_amount(
                {"basis_points": 100, "maximum_fee": 100}, True
            )

    def test_transfer_fee_config_rejects_wrong_extension_length(self):
        with self.assertRaisesRegex(ValueError, "108 bytes"):
            inspector.decode_transfer_fee_config(mint_data(tlv(1, bytes(107))))

    def test_transfer_fee_config_rejects_basis_points_over_10000(self):
        config = bytearray(108)
        config[88:90] = (10_001).to_bytes(2, "little")
        with self.assertRaisesRegex(ValueError, "basis points"):
            inspector.decode_transfer_fee_config(mint_data(tlv(1, bytes(config))))

    def test_parses_transfer_fee_and_permanent_delegate_extensions(self):
        raw = mint_data(tlv(1, bytes(108)) + tlv(12, bytes(32)))
        found = inspector.parse_tlv_extensions(raw, inspector.MINT_ACCOUNT_TYPE)
        self.assertEqual([item["name"] for item in found],
                         ["TRANSFER_FEE_CONFIG", "PERMANENT_DELEGATE"])
        self.assertIsNotNone(found[0]["risk"])
        self.assertIsNotNone(found[1]["risk"])

    def test_recognizes_confidential_mint_burn_and_permissioned_burn(self):
        raw = mint_data(tlv(24, bytes(64)) + tlv(28, bytes(32)))
        found = inspector.parse_tlv_extensions(raw, inspector.MINT_ACCOUNT_TYPE)
        self.assertEqual([item["name"] for item in found],
                         ["CONFIDENTIAL_MINT_BURN", "PERMISSIONED_BURN"])
        self.assertTrue(all(item["risk"] for item in found))

    def test_accepts_base_token2022_mint_without_extensions(self):
        raw = bytearray(82)
        raw[44] = 9
        raw[45] = 1
        with patch("token2022_inspector._read_account",
                   return_value={"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}):
            result = inspector.inspect_token2022_mint(key(4))
        self.assertEqual(result["layout"], "BASE_MINT_NO_EXTENSIONS")
        self.assertEqual(result["decimals"], 9)
        self.assertEqual(result["swap_eligibility"],
                         "BLOCKED_UNTIL_PROTOCOL_SPECIFIC_TOKEN_2022_SUPPORT")

    def test_mint_inspection_exposes_transfer_fee_schedules_without_approval(self):
        config = bytearray(108)
        config[72:80] = (5).to_bytes(8, "little")
        config[80:88] = (100).to_bytes(8, "little")
        config[88:90] = (75).to_bytes(2, "little")
        config[90:98] = (8).to_bytes(8, "little")
        config[98:106] = (200).to_bytes(8, "little")
        config[106:108] = (125).to_bytes(2, "little")
        raw = mint_data(tlv(1, bytes(config)))
        with patch("token2022_inspector._read_account",
                   return_value={"owner": chain.TOKEN_2022_PROGRAM, "data": raw}):
            result = inspector.inspect_token2022_mint(key(10))
        self.assertEqual(result["transfer_fee_config"]["older"]["basis_points"], 75)
        self.assertEqual(result["transfer_fee_config"]["newer"]["basis_points"], 125)
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(
            result["swap_eligibility"],
            "BLOCKED_UNTIL_PROTOCOL_SPECIFIC_TOKEN_2022_SUPPORT",
        )

    def test_inspect_reports_extension_but_never_approves_swap(self):
        raw = mint_data(tlv(14, bytes(64)))
        with patch("token2022_inspector._read_account",
                   return_value={"owner": chain.TOKEN_2022_PROGRAM, "data": raw}):
            result = inspector.inspect_token2022_mint(key(5))
        self.assertEqual(result["extensions"][0]["name"], "TRANSFER_HOOK")
        self.assertEqual(result["execution_enabled"], False)
        self.assertEqual(result["signing"], "NOT_PERFORMED")

    def test_inspects_extended_token_account_without_approving_swaps(self):
        raw = bytearray(166)
        raw[:32] = chain._b58decode(key(8))
        raw[32:64] = chain._b58decode(key(9))
        raw[64:72] = (1234).to_bytes(8, "little")
        raw[108] = 1
        raw[165] = inspector.TOKEN_ACCOUNT_TYPE
        raw.extend(tlv(2, bytes(8)))
        with patch("token2022_inspector._read_account",
                   return_value={"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}):
            result = inspector.inspect_token2022_account(key(7))
        self.assertEqual(result["raw_balance"], 1234)
        self.assertEqual(result["extensions"][0]["name"], "TRANSFER_FEE_AMOUNT")
        self.assertFalse(result["execution_enabled"])

    def test_zero_fee_mint_compatibility_accepts_only_immutable_zero_fee_schedules(self):
        config = bytearray(108)
        raw = mint_data(tlv(1, bytes(config)))
        result = inspector.validate_zero_fee_mint_data(raw)
        self.assertEqual(result["transfer_fee_config"]["older"]["basis_points"], 0)
        config[88:90] = (1).to_bytes(2, "little")
        with self.assertRaisesRegex(ValueError, "must be zero"):
            inspector.validate_zero_fee_mint_data(mint_data(tlv(1, bytes(config))))
        config = bytearray(108)
        config[:32] = chain._b58decode(key(19))
        with self.assertRaisesRegex(ValueError, "authority must be permanently disabled"):
            inspector.validate_zero_fee_mint_data(mint_data(tlv(1, bytes(config))))

    def test_zero_fee_mint_rejects_transfer_hooks(self):
        with self.assertRaisesRegex(ValueError, "unsupported by native swap"):
            inspector.validate_zero_fee_mint_data(mint_data(tlv(14, bytes(64))))

    def test_token2022_account_compatibility_allows_only_safe_account_extensions(self):
        mint, authority = key(21), key(22)
        raw = bytearray(166)
        raw[:32] = chain._b58decode(mint)
        raw[32:64] = chain._b58decode(authority)
        raw[64:72] = (500).to_bytes(8, "little")
        raw[108] = 1
        raw[165] = inspector.TOKEN_ACCOUNT_TYPE
        raw.extend(tlv(7, b""))
        account = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}
        self.assertEqual(
            inspector.validate_token2022_account_data(account, mint, authority), 500
        )
        raw = bytearray(raw[:166])
        raw.extend(tlv(15, bytes(1)))
        account = {"owner": chain.TOKEN_2022_PROGRAM, "data": bytes(raw)}
        with self.assertRaisesRegex(ValueError, "unsupported Token-2022 account extension"):
            inspector.validate_token2022_account_data(account, mint, authority)

    def test_rejects_non_token2022_account(self):
        with patch("token2022_inspector._read_account",
                   return_value={"owner": chain.TOKEN_PROGRAM, "data": bytes(165)}):
            with self.assertRaisesRegex(ValueError, "not owned"):
                inspector.inspect_token2022_account(key(7))

    def test_rejects_wrong_account_discriminator(self):
        raw = bytearray(mint_data(tlv(1, bytes(108))))
        raw[165] = inspector.TOKEN_ACCOUNT_TYPE
        with self.assertRaisesRegex(ValueError, "discriminator"):
            inspector.parse_tlv_extensions(raw, inspector.MINT_ACCOUNT_TYPE)

    def test_rejects_nonzero_mint_padding(self):
        raw = bytearray(mint_data(tlv(1, bytes(108))))
        raw[90] = 1
        with self.assertRaisesRegex(ValueError, "padding"):
            inspector.parse_tlv_extensions(raw, inspector.MINT_ACCOUNT_TYPE)

    def test_rejects_truncated_tlv_value(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            inspector.parse_tlv_extensions(mint_data(b"\\x01\\x00\\x10\\x00x"),
                                            inspector.MINT_ACCOUNT_TYPE)

    def test_rejects_duplicate_extension(self):
        raw = mint_data(tlv(12, bytes(32)) + tlv(12, bytes(32)))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            inspector.parse_tlv_extensions(raw, inspector.MINT_ACCOUNT_TYPE)

    def test_rejects_account_extension_on_mint(self):
        with self.assertRaisesRegex(ValueError, "wrong-account-type"):
            inspector.parse_tlv_extensions(mint_data(tlv(2, bytes(8))),
                                            inspector.MINT_ACCOUNT_TYPE)

    def test_rejects_invalid_public_key_and_non_token2022_owner(self):
        with self.assertRaisesRegex(ValueError, "public key"):
            inspector.inspect_token2022_mint("bad")
        with patch("token2022_inspector._read_account",
                   return_value={"owner": chain.TOKEN_PROGRAM, "data": mint_data()}):
            with self.assertRaisesRegex(ValueError, "not owned"):
                inspector.inspect_token2022_mint(key(6))


if __name__ == "__main__":
    unittest.main()
