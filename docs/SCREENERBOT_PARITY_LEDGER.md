# ScreenerBot functional parity ledger

## Baseline inventory

- ScreenerBot: https://github.com/farfary/ScreenerBot
- ScreenerBot revision: `9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1f` (`main`)
- Trojan: https://github.com/iteev777/solana-ai-trader777
- Trojan baseline revision before this ledger: `e52fc52ed598dcbbaab89bc267822c2c8ada9ef0`
- Trojan working branch: `feature/dex-jupiter-noncustodial-execution-current`
- Latest parity ledger revision before this update: `d854de0d45ebd2d20c7dfa97d2514324bcb3fa33`
- Latest working branch revision before this update: `a29284c51f58bb01d00a78dcbfc8346395cc8260`

### Repository-wide tree inventory

| Inventory | ScreenerBot | Trojan |
|---|---:|---:|
| Tracked files | 3,043 | 212 |
| Main source directory | `src/`: 1,660 | Root Python modules + `discovery/` |
| Tests | `tests/`: 186 Rust/integration test files, plus `tools/tests/` | 100 `tests/` files |
| Locales | 609 | No equivalent locale tree identified |
| Desktop shell | `electron/`: 35 | No equivalent desktop shell identified |
| Tools and fixtures | `tools/`: 444 | No equivalent tools tree identified |
| Images/icons/assets | `icons/`: 71; `assets/`: 10 | No equivalent large asset tree identified |
| Build/workflow/docs/config | Present across root and hidden dirs | Present across root and hidden dirs |

Trojan inventory at this revision includes 185 Python files, 2 Rust files, 1 TypeScript file, 10 Markdown files, 9 workflow YAML files and other configuration/assets. Counts are an inventory, not a parity score.

ScreenerBot is substantially larger because its repository includes a full Rust application, Electron shell, translations, UI assets, fixture corpora, tooling and tests. These must be classified individually as: (a) runtime feature with a Trojan equivalent, (b) runtime feature missing from Trojan, (c) platform-specific feature not applicable to Trojan's deployment model, (d) test/fixture/build support, or (e) non-code asset. Do not silently omit categories.

## Rules

1. Compare behavior and responsibility, not filename or programming language.
2. Inspect the complete reference and Trojan implementation plus relevant tests before changing a module.
3. Implement independently in Trojan's existing Python architecture. ScreenerBot's repository identifies itself as BUSL-1.1; do not copy or adapt its source text or code.
4. Preserve existing Trojan behavior unless a verified defect or parity gap justifies a change.
5. Every code change must include a complete-file replacement in the working record, a commit headline, focused tests and full validation.
6. A module is not complete merely because a test passes. Record feature coverage, edge cases, failure handling and integration status.
7. Keep real-money execution locked. This audit and its tests must remain paper/simulation-only.
8. Every tracked file in both trees must receive an inventory disposition. Non-code assets, generated files and platform-specific files still need a disposition even if they do not require a Trojan code counterpart.

## Functional work status

| Functional area | Reference surface inspected | Trojan surface inspected | Status |
|---|---|---|---|
| Discovery feeds and aggregation | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs`, `src/apis/geckoterminal/endpoints.rs` | `discovery/token_aggregator.py`, `discovery/screenerbot_sources.py`, `discovery/gecko_scanner.py`, focused tests | Partial inspection; recently-updated feed and both pool-side discovery added; no acceptance sign-off |
| Filtering pipeline | `src/filtering/stage.rs`, `src/chains/solana/filtering.rs`, `src/filtering/sources/rugcheck.rs`, `src/filtering/sources/market.rs`, `src/filtering/sources/dexscreener.rs`, `src/filtering/sources/geckoterminal.rs` | `filter_pipeline.py`, `main.py`, `rugcheck.py`, `token_security.py`, focused tests | Partial comparison; RugCheck/on-chain token gates moved before strategy scoring; deeper feature parity remains open |
| API clients and rate limiting | API manager and provider clients | `api_client.py`, provider scanners | Not audited end-to-end |
| Trading admission and exits | Trader, safety gates, entry/exit evaluators | `main.py`, `trading_manager.py`, `entry_intelligence.py`, `exit_policy.py`, `risk_manager.py` | Not audited |
| Positions and accounting | Positions lifecycle, DCA, partial exits, P&L | `position_policy.py`, `paper_trading.py`, `database.py` | Not audited |
| Copy trading and wallet watch | Copy tasks, shared wallet watch, durable cursor/replay handling | `copy_*.py`, `copy_event_parser.py`, `sniper_scanner.py` | Not audited |
| Pool pricing and decoders | Pool service and native DEX decoders | `pool_*.py`, DEX quote/builders, `onchain_stream.py` | Not audited |
| Swap routing and execution | Router comparison, direct swaps, verification | `dex_*.py`, `protocol_quotes.py`, `route_comparison.py`, execution builders | Not audited |
| OHLCV and indicators | Candle service, timeframes, indicators | `market_data.py`, `indicators.py`, `ohlcv_integrity.py` | Not audited |
| Strategies and learning | Strategy conditions/rule trees, AI scoring | `strategy*.py`, `adaptive_learning.py`, `ai_analyzer.py` | Not audited |
| Dashboard and APIs | Webserver, dashboard UI, API contracts | `dashboard.py` | Not audited |
| Service lifecycle and health | Service manager, dependency readiness, monitoring | `service_health.py`, `main.py` | Not audited |
| Database, backup and persistence | SQLite schemas, migrations, backup/restore | `database.py` | Not audited |
| Wallet/security/Token-2022 | Wallets, signer safety, token inspection | `wallet_preflight.py`, `token2022_inspector.py`, `ata_manager.py`, `token_security.py` | Not audited |
| Telegram and notifications | Telegram commands and alerts | `telegram_alerts.py` | Not audited |
| Configuration and deployment | Config schema, installer, workflows, desktop shell | root config, `.github/workflows/`, docs | Not audited |
| Locales, icons, screenshots and other assets | `locales/`, `icons/`, `assets/` | No full counterpart inventory completed | Not audited/disposition pending |
| Tools, fixtures and test corpus | `tools/`, `tests/` | `tests/`, `benchmarks/` | Not audited end-to-end |

## Initial discovery findings (not final)

- Trojan has separate feed functions for DexScreener profiles/latest boosts/top boosts; RugCheck new/recent/trending/verified; Jupiter recent/organic/traded/trending; CoinGecko; and DeFiLlama, in addition to GeckoTerminal, Pump.fun and Birdeye scanners.
- ScreenerBot's GeckoTerminal recently-updated-token feed is implemented in Trojan as `scan_recently_updated_tokens()` using `tokens/info_recently_updated?network=solana`; the aggregator registers it as `geckoterminal_recently_updated`. The scanner validates response shape and Solana identity, deduplicates addresses, preserves metadata, and raises failures for source-health accounting. Focused tests cover request scoping, malformed entries, invalid response shapes and transport failures.
- ScreenerBot's GeckoTerminal pool discovery extracts both base and quote token IDs. Trojan originally extracted only the base token. `discovery/gecko_scanner.py` now discovers both sides, deduplicates them, and attaches base-denominated market data only to the base token. Focused tests cover both sides and duplicate IDs.
- ScreenerBot's discovery runner demonstrates configurable source enablement, chain-adapter feed registration, shutdown-aware scheduling, skip behavior during confirmed network outages/tool contention, and structured run statistics. These behaviors need explicit mapping to Trojan before discovery can be marked complete.
- ScreenerBot's canonical filter sequence is `meta -> symbols -> Solana on-chain -> market -> RugCheck -> LLM`. Trojan's local filter sequence currently covers metadata/symbol checks, market, age, valuation, pool metrics, activity, momentum, provenance and quality. It previously ran RugCheck and RPC token-security checks only after a BUY strategy decision. This ordering defect is now corrected: `main.py` runs RugCheck and configured on-chain mint/account checks after local filters but before candidate observation and strategy scoring. The same fetched RugCheck report is propagated into the approved result.
- On-chain token-security checks remain conditional on RPC configuration for backwards-compatible paper-mode operation. Set `TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY=true` to fail closed when RPC is absent. The default remains false; this is an explicit remaining configuration difference from ScreenerBot's cached on-chain metadata stage, not claimed as full parity.
- ScreenerBot's Solana on-chain stage also checks known scam freeze/update/mint authorities, creator reputation, immutable metadata combined with freeze authority, and a configurable combined risk score. Trojan's `token_security.py` checks mint/freeze authorities and largest token-account/authority concentration, but the same cached authority blacklist and combined-risk scoring parity is not yet established.
- ScreenerBot's RugCheck source exposes configurable gates for rugged status, risk score/level, mint/freeze authorities, holder distribution, insiders, graph insiders, creator balance, transfer fees, LP providers and LP lock. Trojan's `check_rugcheck()` has strict PASS/safe/score/high-risk checks, while `rugcheck.py` contains additional extensions; the full field-by-field configuration and semantics comparison remains open.
- This is an initial sample, not a claim that all discovery files or edge cases have been inspected.

## Work completed in this audit session (filtering continuation)

- `main.py`: added `evaluate_pre_strategy_security()` and moved RugCheck plus configured on-chain mint/account security checks ahead of candidate observation and strategy scoring. RugCheck transport failures, RPC status failures, account blacklist hits and on-chain report failures reject safely. Added opt-in fail-closed behavior when RPC is missing via `TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY=true` (default false preserves existing paper behavior).
- Added `tests/test_pre_strategy_security.py` with seven focused tests for RugCheck rejection, provider failure, RPC absent in both modes, RPC status failure, on-chain rejection and successful configured checks.
- GitHub Actions for commit `a29284c51f58bb01d00a78dcbfc8346395cc8260`: 891 unit tests passed; full validation passed including module compilation, live execution safety locks, dashboard smoke checks and paper-only status (run `45243701804`).
- Commit headlines from this session: `fix(security): run token risk gates before strategy scoring`; `test(security): cover pre-strategy risk gate ordering and failures`.
- Discovery changes already validated in prior session: 884 tests passed and full validation succeeded on commit `86782fd736e7148f3bd9a0fe738a81cff61fc4aa` (run `45242963828`).

## Completion criteria

A functional area can be marked complete only after:
- every relevant reference file has a mapped Trojan implementation or an explicit justified N/A;
- every relevant Trojan file has been reviewed, including tests and configuration;
- every tracked repository file has an inventory disposition;
- missing capabilities have been independently implemented where appropriate;
- tests cover success, malformed data, empty data, duplicates, rate limits, provider failures, partial source failures and recovery where relevant;
- integration and full CI validation pass; and
- resulting commit SHA and changed-file list are recorded here.

The repositories are not considered parity-complete until every row and every file disposition is closed.