# ScreenerBot parity review

Trojan baseline: `41b33dc` (draft PR #116). ScreenerBot reference:
`9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1f`.

[Reference review status](SCREENERBOT_REVIEW_STATUS.tsv) gives each tracked
ScreenerBot path a status. `reviewed` requires reading the complete file and
recording a functional comparison. Files listed only in the earlier
[functional inventory](screenerbot-file-inventory.tsv) retain their unreviewed
status in this review.

| Trojan file | ScreenerBot counterpart | Finding and remaining gaps | Change and commit | Validation |
| --- | --- | --- | --- | --- |
| `discovery/token_aggregator.py` | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs` | Both merge concurrent provider results and isolate provider errors. Trojan had no byte-level mint check or valid/invalid counts. Reference has per-feed configuration, rate-lane selection, chain-specific discovery, persistent upserts, blacklist/existing-record checks and cancellable futures. Trojan still hands candidates to downstream paper ranking and filtering. | `11c0a5a` Validate discovery candidates and publish consistent scan metrics. Validate public-key bytes, exclude quote assets, report counts and duration, and publish consistent status without exposing provider exception text. | Syntax and diff checks passed. Added mocked invalid input and status isolation coverage. Suite reserved for batch checkpoint. |
