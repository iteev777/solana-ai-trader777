# Trojan V2 — Test Readiness

## Current gate

The full test suite is intentionally **not run during the ScreenerBot parity audit**.
The GitHub Actions workflow `.github/workflows/tests.yml` is manual-only
(`workflow_dispatch`) so ordinary audit commits cannot start premature test runs.

Do not open the final test gate until:
- Every entry in `docs/TROJAN_FILE_AUDIT_INVENTORY.md` is `UPDATED` or
  `NO-CHANGE`; no `PENDING` or `PARTIAL` entries remain.
- Missing Trojan-side functionality has either been implemented or explicitly
  documented as intentionally out of scope, with the non-custodial safety model
  preserved.
- The ScreenerBot parity ledger and this inventory agree on the final state.
- The live execution hard locks have been reviewed and remain enabled.

## Test environment

- Python: 3.12 (same version as the GitHub Actions workflow).
- Install dependencies with `python -m pip install -r requirements.txt`.
- Unit tests use the standard-library `unittest` runner.
- Run the complete suite with:

  ```bash
  python -m unittest discover -s tests -v
  ```

- Run the suite only after the audit is complete. The same command is used by
  the manual GitHub Actions workflow.
- Keep API calls mocked in unit tests. Do not use funded wallets, mainnet
  signing keys, mainnet transaction submission, or real-money execution as
  part of the unit-test run.
- The separate signed Devnet E2E harness is not part of the default
  `unittest discover` command. It requires its own explicit review and
  opt-in; do not execute it automatically with the unit suite.

## Safety invariants to verify at the final gate

- Paper trading remains the normal operating mode.
- Native broadcast and Jupiter V2 live execution remain hard-locked in code.
- No environment variable or per-request confirmation can unlock live execution.
- Unsigned transaction construction/simulation must not be reported as a
  successful signed or executed trade.
- Tests must not require private keys or secrets in repository files or CI
  variables.

## Final sequence

1. Complete all source and reference comparisons.
2. Resolve or document every remaining parity gap.
3. Review dependencies, startup/shutdown, persistence and configuration.
4. Run the full unit-test suite once, then fix failures and rerun the full suite.
5. Perform separate, explicitly approved Devnet-only integration checks if
   required.
6. Confirm live execution remains locked and record the final test report.
