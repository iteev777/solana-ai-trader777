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
| `scanner.py` | `src/tokens/discovery_sources.rs`, `src/apis/dexscreener/{mod,endpoints,types}.rs` | Parallel endpoint requests and independent outcomes already existed. Two Trojan entrypoints named the same endpoints differently, inflating source agreement after merge. Reference uses typed responses and endpoint-specific rate budgets. The latter needs separate API client review. | `21eac6a` Normalize standalone DexScreener discovery provenance. Reuse canonical labels and mint validation, use monotonic cooldown and fill missing metadata from later results. Community takeovers remain enabled. | Syntax and diff checks passed. Added mocked coverage. |
| `discovery/dexscreener_scanner.py` | `src/tokens/discovery_sources.rs`, `src/apis/dexscreener/{endpoints,types}.rs` | Reference uses typed metadata and retains feed-level outcomes. Trojan preserves provider fields beyond reference candidate records, but its first-hit merge discarded later fields and omitted valid zero boosts. Separate lane and per-feed status remain for the shared client. | `183887e` Retain supplemental DexScreener discovery metadata. Merge missing fields deterministically, retain explicit zero and log exception classes. | Syntax and diff checks passed. Added mocked metadata coverage. |
