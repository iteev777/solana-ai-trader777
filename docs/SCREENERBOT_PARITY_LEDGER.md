# ScreenerBot functional parity ledger

## Baseline inventory

- ScreenerBot: https://github.com/farfary/ScreenerBot
- ScreenerBot revision: `9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1f` (`main`)
- Trojan: https://github.com/iteev777/solana-ai-trader777
- Trojan baseline revision before this ledger: `e52fc52ed598dcbbaab89bc267822c2c8ada9ef0`
- Trojan working branch: `feature/dex-jupiter-noncustodial-execution-current`
- Latest ledger commit: `1815784d3d623737b9a490f6f35986e0669ecdad`

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
| Discovery feeds and aggregation | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs` | `discovery/token_aggregator.py`, `discovery/screenerbot_sources.py`, GeckoTerminal and DexScreener scanners | Partial inspection only; no acceptance sign-off |
| Filtering pipeline | `src/filtering/engine.rs`, RugCheck filter source | `filter_pipeline.py`, `rugcheck.py` | Partial inspection only; no acceptance sign-off |
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
- The inspected ScreenerBot discovery implementation also has a GeckoTerminal recently-updated-token feed. A corresponding independently implemented Trojan feed and its per-source configuration/health/test coverage have not yet been verified.
- ScreenerBot's discovery runner demonstrates configurable source enablement, chain-adapter feed registration, shutdown-aware scheduling, skip behavior during confirmed network outages/tool contention, and structured run statistics. These behaviors need explicit mapping to Trojan before discovery can be marked complete.
- This is an initial sample, not a claim that all discovery files or edge cases have been inspected.

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