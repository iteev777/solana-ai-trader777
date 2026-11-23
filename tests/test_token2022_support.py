import unittest

from token2022_support import (
    TOKEN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    inspect_mint_account,
    transfer_fee_for_amount,
    apply_transfer_fees,
)


class Token2022SupportTests(unittest.TestCase):
    def mint(self, extensions=()):
        data = bytearray(166)
        data[44] = 9
        data[165] = 1
        for extension_type, value in extensions:
            data.extend(int(extension_type).to_bytes(2, "little"))
            data.extend(len(value).to_bytes(2, "little"))
            data.extend(value)
        return {"owner": TOKEN_2022_PROGRAM_ID, "data": bytes(data)}

    def transfer_fee_config(self, older=(100, 1000), newer=(250, 100)):
        value = bytearray(108)
        # authority(32), withdraw authority(32), withheld amount(8)
        value[72:80] = (0).to_bytes(8, "little")
        value[80:88] = older[1].to_bytes(8, "little")
        value[88:90] = older[0].to_bytes(2, "little")
        value[90:98] = (0).to_bytes(8, "little")
        value[98:106] = newer[1].to_bytes(8, "little")
        value[106:108] = newer[0].to_bytes(2, "little")
        return bytes(value)

    def test_legacy_spl_mint_has_no_transfer_fee(self):
        profile = inspect_mint_account({
            "owner": TOKEN_PROGRAM_ID, "data": bytes(82)
        })
        self.assertFalse(profile["is_token_2022"])
        self.assertEqual(transfer_fee_for_amount(12345, profile), 0)

    def test_token_2022_mint_without_extensions_is_supported(self):
        profile = inspect_mint_account({
            "owner": TOKEN_2022_PROGRAM_ID, "data": bytes(82)
        })
        self.assertTrue(profile["is_token_2022"])
        self.assertEqual(profile["transfer_fee_schedules"], [])

    def test_transfer_fee_config_parses_both_epoch_schedules(self):
        profile = inspect_mint_account(self.mint([
            (1, self.transfer_fee_config())
        ]))
        self.assertEqual(
            [(x["schedule"], x["basis_points"], x["maximum_fee"])
             for x in profile["transfer_fee_schedules"]],
            [("older", 100, 1000), ("newer", 250, 100)],
        )

    def test_fee_is_ceiling_rounded_and_capped_using_maximum_epoch_fee(self):
        profile = inspect_mint_account(self.mint([
            (1, self.transfer_fee_config())
        ]))
        self.assertEqual(transfer_fee_for_amount(1, profile), 1)
        self.assertEqual(transfer_fee_for_amount(10_000, profile), 100)
        self.assertEqual(transfer_fee_for_amount(100_000, profile), 1000)

    def test_zero_fee_schedule_returns_zero(self):
        profile = inspect_mint_account(self.mint([
            (1, self.transfer_fee_config((0, 0), (0, 0)))
        ]))
        self.assertEqual(transfer_fee_for_amount(999_999, profile), 0)

    def test_transfer_fee_application_adjusts_both_directions(self):
        profile = inspect_mint_account(self.mint([
            (1, self.transfer_fee_config())
        ]))
        legacy = inspect_mint_account({
            "owner": TOKEN_PROGRAM_ID, "data": bytes(82)
        })
        result = apply_transfer_fees(10_000, 5_000, profile, profile)
        self.assertEqual(result, {
            "net_input": 9_900,
            "net_output": 4_900,
            "input_fee": 100,
            "output_fee": 100,
        })
        no_fee = apply_transfer_fees(10_000, 5_000, legacy, legacy)
        self.assertEqual(no_fee["net_input"], 10_000)
        self.assertEqual(no_fee["net_output"], 5_000)

    def test_active_transfer_hook_is_refused(self):
        hook = bytes(32) + bytes([7]) * 32
        with self.assertRaisesRegex(ValueError, "transfer hooks"):
            inspect_mint_account(self.mint([(14, hook)]))

    def test_zero_transfer_hook_program_is_allowed(self):
        profile = inspect_mint_account(self.mint([(14, bytes(64))]))
        self.assertFalse(profile["transfer_hook"])

    def test_non_transferable_mint_is_refused(self):
        with self.assertRaisesRegex(ValueError, "confidential or transfer-restricting"):
            inspect_mint_account(self.mint([(9, b"")]))

    def test_frozen_default_account_state_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Frozen"):
            inspect_mint_account(self.mint([(6, bytes([2]))]))

    def test_initialized_default_account_state_is_allowed(self):
        profile = inspect_mint_account(self.mint([(6, bytes([1]))]))
        self.assertIn(6, profile["extensions"])

    def test_confidential_transfer_mint_is_refused(self):
        with self.assertRaisesRegex(ValueError, "confidential or transfer-restricting"):
            inspect_mint_account(self.mint([(4, bytes(65))]))

    def test_paused_mint_is_refused(self):
        with self.assertRaisesRegex(ValueError, "currently paused"):
            inspect_mint_account(self.mint([(26, bytes(32) + bytes([1]))]))

    def test_unpaused_mint_is_allowed(self):
        profile = inspect_mint_account(self.mint([(26, bytes(32) + bytes([0]))]))
        self.assertFalse(profile["paused"])

    def test_permanent_delegate_is_reported(self):
        profile = inspect_mint_account(self.mint([(12, bytes([9]) * 32)]))
        self.assertTrue(profile["permanent_delegate"])

    def test_known_metadata_extension_does_not_change_raw_transfer_amounts(self):
        profile = inspect_mint_account(self.mint([(18, bytes(64))]))
        self.assertEqual(transfer_fee_for_amount(5000, profile), 0)

    def test_unknown_extension_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unrecognized extension"):
            inspect_mint_account(self.mint([(250, b"abc")]))

    def test_duplicate_extension_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate extension"):
            inspect_mint_account(self.mint([(18, bytes(64)), (18, bytes(64))]))

    def test_invalid_account_type_and_truncated_header_are_rejected(self):
        data = bytearray(self.mint([(18, bytes(64))])["data"])
        data[165] = 2
        with self.assertRaisesRegex(ValueError, "account type"):
            inspect_mint_account({"owner": TOKEN_2022_PROGRAM_ID, "data": bytes(data)})
        with self.assertRaisesRegex(ValueError, "header is truncated"):
            inspect_mint_account({"owner": TOKEN_2022_PROGRAM_ID, "data": bytes(100)})

    def test_malformed_transfer_fee_config_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly 108"):
            inspect_mint_account(self.mint([(1, bytes(107))]))

    def test_invalid_basis_points_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceed 10000"):
            inspect_mint_account(self.mint([
                (1, self.transfer_fee_config((10001, 1000), (0, 0)))
            ]))

    def test_unsupported_token_program_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported token program"):
            inspect_mint_account({"owner": "not-a-token-program", "data": bytes(82)})


if __name__ == "__main__":
    unittest.main()
