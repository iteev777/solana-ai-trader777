# ScreenerBot functional parity ledger

## Baseline

- Reference repository: https://github.com/farfary/ScreenerBot
- Reference revision audited: `9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1f` (`main`)
- Trojan repository: https://github.com/iteev777/solana-ai-trader777
- Trojan revision audited: `e52fc52ed598dcbbaab89bc267822c2c8ada9ef0`
- Trojan branch: `feature/dex-jupiter-noncustodial-execution-current`
- Reference tree: 3,043 tracked files; 1,660 files under `src/`.
- Trojan tree: 211 tracked files; 188 code files; 100 test files.

Counts are inventory counts, not parity scores. Reference assets, locales, generated files, desktop shell and Rust modules must be classified separately; file-count equality is neither possible nor a useful acceptance criterion.

## Rules

1. Compare behavior and responsibility, not filename or programming language.
2. Inspect the complete reference and Trojan implementation plus relevant tests before changing a module.
3. Implement independently in Trojan's existing Python architecture. Do not copy or adapt BUSL-1.1 source text or code. The reference repository identifies itself as BUSL-1.1.
4. Preserve existing Trojan behavior unless a verified defect or parity gap justifies a change.
5. Every code change must include a complete-file replacement in the working record, a commit headline, focused tests and full validation.
6. A module is not complete merely because a test passes. Record feature coverage, edge cases, failure handling and integration status.
7. Keep real-money execution locked. This audit and its tests must remain paper/simulation-only.

## Work status

| Functional area | Reference surface inspected | Trojan surface inspected | Status |
|---|---|---|---|
| Discovery feeds and aggregation | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs` | `discovery/token_aggregator.py`, `discovery/screenerbot_sources.py`, GeckoTerminal and DexScreener scanners | In progress; no acceptance sign-off |
| Filtering pipeline | `src/filtering/engine.rs`, RugCheck filter source | `filter_pipeline.py`, `rugcheck.py` | In progress; full behavior matrix still required |
| API clients and rate limiting | API manager and provider clients | `api_client.py`, provider scanners | Not started as a complete file-by-file pass |
| Remaining subsystems | To inventory by responsibility | To map against Trojan files | Not started |

## Initial discovery findings (not final)

- Trojan already has separate feed functions for DexScreener profiles/latest boosts/top boosts; RugCheck new/recent/trending/verified; Jupiter recent/organic/traded/trending; CoinGecko; and DeFiLlama, in addition to GeckoTerminal, Pump.fun and Birdeye scanners.
- The inspected ScreenerBot discovery implementation also has a GeckoTerminal recently-updated-token feed. A corresponding independently implemented Trojan feed and its per-source configuration/health/test coverage have not yet been verified.
- ScreenerBot's discovery runner additionally demonstrates configurable source enablement, chain-adapter feed registration, shutdown-aware scheduling, skip behavior during confirmed network outages/tool contention, and structured run statistics. These behaviors need explicit mapping to Trojan before the discovery area can be marked complete.
- Initial inspection is not a claim that every feed or edge case is equivalent.

## Completion criteria

A functional area can be marked complete only after:
- every relevant reference file has a mapped Trojan implementation or an explicit justified N/A;
- every relevant Trojan file has been reviewed, including tests and configuration;
- missing capabilities have been independently implemented where appropriate;
- tests cover success, malformed data, empty data, duplicates, rate limits, provider failures, partial source failures and recovery where relevant;
- integration and full CI validation pass; and
- the resulting commit SHA and changed-file list are recorded here.
