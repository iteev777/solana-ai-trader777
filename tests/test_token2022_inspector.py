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
