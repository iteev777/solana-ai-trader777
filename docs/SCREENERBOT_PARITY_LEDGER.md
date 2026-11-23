# ScreenerBot functional parity ledger

## Baseline inventory

- ScreenerBot: https://github.com/farfary/ScreenerBot
- ScreenerBot revision: `9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1f` (`main`)
- Trojan: https://github.com/iteev777/solana-ai-trader777
- Trojan baseline revision before this ledger: `e52fc52ed598dcbbaab89bc267822c2c8ada9ef0`
- Trojan working branch: `feature/dex-jupiter-noncustodial-execution-current`
- Latest parity ledger revision before this update: `2b231b1b772f2e109e118c06cb3b6cd0ab0471fa`
- Latest working branch revision before this update: `e7bfc0311e7979558b142f140319232bfd910893`

### Repository-wide tree inventory

| Inventory | ScreenerBot | Trojan |
|---|---:|---:|
| Tracked files | 3,043 | 218 |
| Main source directory | `src/`: 1,660 | Root Python modules + `discovery/` |
| Tests | `tests/`: 186 Rust/integration test files, plus `tools/tests/` | 106 `tests/` files |
| Locales | 609 | No equivalent locale tree identified |
| Desktop shell | `electron/`: 35 | No equivalent desktop shell identified |
| Tools and fixtures | `tools/`: 444 | No equivalent tools tree identified |
| Images/icons/assets | `icons/`: 71; `assets/`: 10 | No equivalent large asset tree identified |
| Build/workflow/docs/config | Present across root and hidden dirs | Present across root and hidden dirs |

Trojan inventory at the latest audited revision includes 191 Python files, 2 Rust files, 1 TypeScript file, 10 Markdown files, 9 workflow YAML files and other configuration/assets. Counts are an inventory, not a parity score.

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
| Discovery feeds and aggregation | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs`, `src/apis/geckoterminal/endpoints.rs` | `discovery/token_aggregator.py`, `discovery/screenerbot_sources.py`, `discovery/gecko_scanner.py`, `tests/test_token_aggregator.py`, `docs/DISCOVERY_QUALITY.md` | Partial inspection; recently-updated feed, both pool sides, and provider/global switches added; dynamic runtime configuration and broader runner parity remain open |
| Filtering pipeline | `src/filtering/stage.rs`, `src/chains/solana/filtering.rs`, `src/filtering/sources/symbols.rs`, `src/filtering/sources/meta.rs`, `src/filtering/sources/rugcheck.rs`, `src/filtering/sources/market.rs`, `src/filtering/sources/dexscreener.rs`, `src/filtering/sources/geckoterminal.rs`, `src/filtering/sources/llm_analysis.rs` | `filter_pipeline.py`, `main.py`, `rugcheck.py`, `token_security.py`, `ai_analyzer.py`, focused tests | Partial comparison; pre-strategy RugCheck/on-chain gates, metadata preservation and initial Solana on-chain stage added; market/meta parity remains open |
| API clients and rate limiting | `src/apis/client.rs`, `src/apis/manager.rs`, `src/apis/stats.rs`, provider clients | `api_client.py`, provider scanners, `tests/test_api_client_stats.py`, `dashboard.py` | Partial comparison; per-host/per-endpoint request/cache statistics added; provider-specific manager semantics and retry telemetry remain open |
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
- ScreenerBot's discovery runner demonstrates runtime-configurable source enablement, chain-adapter feed registration, shutdown-aware scheduling, skip behavior during confirmed network outages/tool contention, and structured run statistics. Trojan has static-at-process-start environment switches for provider and per-feed enablement, a global discovery switch, parallel isolated fetches, persisted per-source health/latency, source-family consensus, deduplication and candidate persistence. Trojan still lacks the reference's explicit shutdown-raced discovery loop and confirmed-offline/tool-contention skip behavior; its switches are not re-read dynamically without process restart.
- ScreenerBot's canonical filter sequence is `meta -> symbols -> Solana on-chain -> market -> RugCheck -> LLM`. Trojan's local filter sequence currently covers metadata/symbol checks, market, age, valuation, pool metrics, activity, momentum, provenance and quality. It previously ran RugCheck and RPC token-security checks only after a BUY strategy decision. This ordering defect is now corrected: `main.py` runs RugCheck and configured on-chain mint/account checks after local filters but before candidate observation and strategy scoring. The same fetched RugCheck report is propagated into the approved result.
- On-chain token-security checks remain conditional on RPC configuration for backwards-compatible paper-mode operation. Set `TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY=true` to fail closed when RPC is absent. The default remains false; this is an explicit remaining configuration difference from ScreenerBot's cached on-chain metadata stage, not claimed as full parity.
- ScreenerBot's Solana on-chain stage checks known scam freeze/update/mint authorities, creator reputation, immutable metadata combined with freeze authority, and a configurable combined risk score. Trojan now runs a lightweight `onchain` filter stage before market filtering; it checks the known-scam-authority flag, immutable metadata with active freeze authority, and a configurable combined score. `main.py` also checks candidate mint/freeze/update/creator authority addresses against the persistent account blacklist before market filtering. This is partial parity: there is no complete cached authority reputation/creator scoring service equivalent yet. `token_security.py` additionally checks mint/freeze authorities and largest token-account/authority concentration through RPC.
- ScreenerBot's RugCheck source exposes configurable gates for rugged status, risk score/level, mint/freeze authorities, holder distribution, insiders, graph insiders, creator balance, transfer fees, LP providers and LP lock. Trojan's `rugcheck.py` independently implements many of these signals (including top-holder/top-10/top-3 concentration, insiders, graph insiders, creator balance, transfer fees, LP providers and LP lock); the full field-by-field configuration and semantics comparison remains open.
- ScreenerBot's Solana filter profile order is `meta -> symbols -> onchain -> market -> RugCheck -> LLM`. Trojan now orders its local checks as `metadata -> onchain -> market -> age -> valuation -> pool metrics -> activity -> momentum -> provenance -> quality`, followed by RugCheck/on-chain token-security before observer/strategy. The empty-symbol rule now rejects by default; numeric-only symbols reject; suspicious non-letter single-character symbols are disabled by default to match ScreenerBot's default (can be enabled with `TROJAN_FILTER_REJECT_SINGLE_CHAR_SYMBOL=true`).
- A confirmed metadata propagation gap was fixed: `main.py` now fills missing/placeholder market-pair name/symbol/logo/website/authority fields from discovery candidates while preserving real market provider values. It also preserves the verified nested base-token name/symbol over conflicting discovery metadata.
- Remaining filter gaps: ScreenerBot's `MetaStage` resolves and requires valid decimals and applies a default 180-minute age and cooldown; Trojan's age floor defaults to zero and no equivalent decimals-resolution/cooldown stage has been verified. ScreenerBot's DexScreener filter defaults are source-specific (e.g. $15k minimum liquidity, $50k minimum market cap, transaction and volume floors, logo required), while Trojan's current market filter is provider-agnostic and has looser defaults. GeckoTerminal is disabled by default in ScreenerBot filtering; Trojan currently accepts Gecko market snapshots through its own fallback path. These are open behavior differences, not accepted parity.

- API client comparison: ScreenerBot centralizes provider clients in a singleton API manager and exposes total/success/failure/cache hit/cache miss/average-latency statistics per client. Trojan already had host/endpoint rate buckets, serialized locks, shared 429 cooldown, Retry-After parsing, retries, bounded cache and host status; it lacked equivalent per-endpoint request statistics. `api_client.py` now tracks total/success/failure, cache hits/misses, last request/success/error, last error message, average response time and derived success/cache-hit rates per host and endpoint. `dashboard.py` already returns `get_api_status()` in `/api/overview`, so the new stats are exposed without a separate route. Remaining API differences include no dedicated per-provider client objects/manager and no separate retry/429/status-code counters in the stats payload.
- This is an initial sample, not a claim that all discovery files or edge cases have been inspected.

## Work completed in this audit session (filtering and discovery continuation)

- `main.py`: added `evaluate_pre_strategy_security()` and moved RugCheck plus configured on-chain mint/account security checks ahead of candidate observation and strategy scoring. RugCheck transport failures, RPC status failures, account blacklist hits and on-chain report failures reject safely. Added opt-in fail-closed behavior when RPC is missing via `TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY=true` (default false preserves existing paper behavior).
- Added `tests/test_pre_strategy_security.py` with seven focused tests for RugCheck rejection, provider failure, RPC absent in both modes, RPC status failure, on-chain rejection and successful configured checks.
- GitHub Actions for commit `a29284c51f58bb01d00a78dcbfc8346395cc8260`: 891 unit tests passed; full validation passed including module compilation, live execution safety locks, dashboard smoke checks and paper-only status (run `45243701804`).
- Commit headlines from this session: `fix(security): run token risk gates before strategy scoring`; `test(security): cover pre-strategy risk gate ordering and failures`.
- Discovery feed parity from the preceding pass: 884 tests passed and full validation succeeded on commit `86782fd736e7148f3bd9a0fe738a81cff61fc4aa` (run `45242963828`).
- `discovery/token_aggregator.py`: added `TROJAN_DISCOVERY_ENABLED`, GeckoTerminal provider/per-feed switches, and Pump.fun/Birdeye provider switches. The new source switches default to enabled and are read at process startup, consistent with existing provider switches.
- `tests/test_token_aggregator.py`: added coverage for global discovery disablement, GeckoTerminal provider/per-feed switches, and independent Pump.fun/Birdeye switches.
- `docs/DISCOVERY_QUALITY.md`: documented all newly introduced environment controls and restart behavior.
- Discovery switch commit headlines: `feat(discovery): add GeckoTerminal and chain feed enablement controls`; `test(discovery): cover provider and global feed controls`; `docs(discovery): document global and per-feed source controls`.
- GitHub Actions for discovery source changes `fd614db36c15d1d15b2b36a58a3369b537534d60`: 894 unit tests passed; full validation passed (run `45244092689`).
- `filter_pipeline.py`: added a dedicated early Solana `onchain` stage ahead of market filters; aligned empty-symbol rejection and single-character default behavior with ScreenerBot; added immutable+freeze and configurable combined-risk checks; standardized boolean environment parsing.
- `main.py`: extended pre-market blacklist matching to candidate mint/freeze/update/creator authorities; added `merge_candidate_metadata()` so discovery metadata fills missing market-provider fields without overwriting verified market/base-token metadata; retained RugCheck and RPC token-security checks before observer/strategy.
- Added/expanded focused tests in `tests/test_filter_pipeline.py`, `tests/test_extended_market_filter_bounds.py`, `tests/test_candidate_metadata_merge.py`, and `tests/test_pre_strategy_security.py`.
- Filtering commit headlines: `feat(filtering): add early Solana on-chain metadata stage`; `feat(security): check known authority blacklist before market filters`; `test(filtering): cover ScreenerBot-style Solana on-chain gates`; `test(filtering): align symbol defaults with ScreenerBot`; `fix(filtering): retain discovery metadata for candidate screening`; `test(filtering): cover discovery metadata enrichment`; `fix(filtering): preserve existing market website metadata`; `test(filtering): preserve existing website fields during merge`; `fix(filtering): preserve verified base token metadata`; `test(filtering): preserve verified base token metadata`; `fix(filtering): normalize on-chain filter boolean settings`; `test(filtering): cover numeric false on-chain config value`; `fix(filtering): standardize boolean environment parsing`.
- Latest verified filtering revision `72495753e928676298230dc5ac7cc49be0d7e776`: 905 unit tests passed and full validation succeeded (run `45245704007`).
- `api_client.py`: added per-host/per-endpoint request statistics and exposed them through the existing dashboard `/api/overview` payload. Stats include successful/failed logical requests, cache hits/misses, average response time, last request/success/error timestamps, last error text and derived rates.
- Added `tests/test_api_client_stats.py` for success/cache-hit behavior, failures and independent endpoint buckets. Stabilized `tests/test_expanded_rugcheck_risk.py` by pinning the creator-share thresholds in the test so deployment-specific environment overrides cannot change its expected severity.
- API commit headlines: `feat(api): add per-host request and cache statistics`; `fix(api): correct per-endpoint stats iteration syntax`; `fix(api): count cache misses only for cache-enabled requests`; `test(api): cover per-host request and cache statistics`; `test(rugcheck): isolate creator risk threshold defaults`.
- Latest verified source revision `e7bfc0311e7979558b142f140319232bfd910893`: 919 unit tests passed and full validation succeeded, including Python module compilation, live-execution safety locks, dashboard smoke checks and paper-only status (run `45246578309`).

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