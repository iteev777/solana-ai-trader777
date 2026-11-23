# Trojan V2 — tiedostokohtainen auditointi

- Trojan-haara: `feature/dex-jupiter-noncustodial-execution-current`
- Vertailurepositorio: `farfary/ScreenerBot` @ `9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1`
- Inventaarion muodostus: 2026-11-23
- Trojanin seurattavat tiedostot: 228
- ScreenerBotin vertailutiedostot: 3043
- Tila: kaikki rivit aloittavat tilasta PENDING; samanniminen polku on vain hakuvihje, ei vahvistettu vastine.

Tilarajat: PENDING = ei tarkastettu; REVIEWED = sisältö ja toiminta luettu; COMPARED = verrattu vastineeseen; UPDATED = Trojan-tiedosto muutettu ja commit kirjattu; TESTED = testit läpäisseet; NO-CHANGE = tarkastettu, ei muutosta perustellusti tarvita; NEW-IMPLEMENTATION = ScreenerBot-ominaisuudelle luotu Trojan-toteutus. Jokaiselle riville kirjataan perustelu ja commit/testiviite ennen valmistumista.

| Trojanin tiedosto | ScreenerBot-vastine-ehdokkaat (ei vahvistettu) | Tila | Havainnot / commit / testit |
|---|---|---|---|
| `.github/workflows/create-playground-token2022-share.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/deploy-token2022-signer.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/devnet-e2e.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/devnet-token2022-e2e.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/devnet-token2022-readonly.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/devnet-token2022-signed-e2e.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/pool-parity-benchmark.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/tests.yml` | Ei vielä kartoitettu | PENDING |  |
| `.github/workflows/trojan-full-validation.yml` | Ei vielä kartoitettu | PENDING |  |
| `.gitignore` | `.gitignore`<br>`electron/.gitignore` | PENDING |  |
| `DEX_EXECUTION.md` | Ei vielä kartoitettu | PENDING |  |
| `README.md` | `README.md`<br>`electron/README.md`<br>`electron/assets/README.md`<br>`src/apis/README.md`<br>`src/apis/llm/deepseek/README.md`<br>`src/ohlcvs/README.md`<br>`src/tokens/README.md` | UPDATED | Documented durable copy-wallet signature catch-up, page size, poll interval, bounded skip-to-head and stale-event behavior. Commit `4672227`; full validation `45254436959`. ScreenerBot docs comparison remains pending. |
| `adaptive_learning.py` | Ei vielä kartoitettu | PENDING |  |
| `ai_analyzer.py` | Ei vielä kartoitettu | PENDING |  |
| `api_client.py` | `src/apis/` request clients | TESTED | Added per-host/per-endpoint request, cache, latency and error telemetry; cache-miss accounting is limited to cache-enabled requests. Tests `test_api_client_stats.py`; full validation run `45254436959` (998 tests).  |
| `ata_manager.py` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/pool_rpc_parity.py` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/Cargo.toml` | `Cargo.toml` | PENDING |  |
| `benchmarks/rust_rpc/README.md` | `README.md`<br>`electron/README.md`<br>`electron/assets/README.md`<br>`src/apis/README.md`<br>`src/apis/llm/deepseek/README.md`<br>`src/ohlcvs/README.md`<br>`src/tokens/README.md` | PENDING |  |
| `benchmarks/rust_rpc/sidecar_smoke.py` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/src/bin/rpc_sidecar.rs` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/src/main.rs` | `src/main.rs` | PENDING |  |
| `blacklist.py` | Ei vielä kartoitettu | PENDING |  |
| `candidate_observer.py` | Ei vielä kartoitettu | PENDING |  |
| `candidate_quality.py` | Ei vielä kartoitettu | PENDING |  |
| `clmm_quote_math.py` | Ei vielä kartoitettu | PENDING |  |
| `copy_event_parser.py` | `src/wallets/watch/runtime.rs`, `src/wallets/watch/types.rs` | TESTED | Conservative raw-balance parser now validates account indexes, unique subject identity, loaded program indexes, one token leg/one quote leg, native SOL and ATA-rent correction; ambiguous/mixed flows fail closed. Tests in `test_copy_event_parser.py`; full validation run `45254436959`. Full reference decoder/runtime comparison remains open.
| `copy_freshness.py` | `src/trader/copy/guards.rs`, `src/trader/copy/analytics.rs` | TESTED | Measures chain-to-observation arrival age from captured receipt time, rejects future/invalid timestamps. Tests `f9b9445`, `17c003f`; full validation run `45254436959` (998 tests).
| `copy_risk.py` | `src/trader/copy/risk.rs`, `src/trader/copy/sizing.rs` | PARTIAL | Full file and focused tests read. Trojan enforces wallet-isolated USD exposure/daily loss/position limits; reference enforces per-task target min/max, filter/self-copy, slippage, buy-once and SOL budget/sizing caps. These are complementary, not equivalent; task-level controls remain open. |
| `copy_trade.py` | `src/wallets/watch/service.rs`, `src/wallets/watch/poller.rs`, `src/trader/copy/service.rs`, `src/trader/copy/guards.rs` | TESTED | Full monitor read. Added bounded paged catch-up, durable per-wallet cursor, oldest-first replay, safe null-index retry, replay dedupe, bounded skip-to-head and stale-event suppression. Tests cover cursor persistence, retry, page order and skip cap; full validation run `45254436959`. Full wallet-watch/copy-task architecture comparison remains open.
| `dashboard.py` | Ei vielä kartoitettu | PENDING |  |
| `database.py` | `src/database/`, `src/positions/db.rs` | PARTIAL | Atomic paper DCA investment and partial-close transaction paths inspected; full database parity audit remains open. |
| `devnet_e2e.py` | Ei vielä kartoitettu | PENDING |  |
| `devnet_token2022_e2e.py` | Ei vielä kartoitettu | PENDING |  |
| `devnet_token2022_signed_e2e.py` | Ei vielä kartoitettu | PENDING |  |
| `dex_capabilities.py` | Ei vielä kartoitettu | PENDING |  |
| `dex_engine.py` | Ei vielä kartoitettu | PENDING |  |
| `dex_execution.py` | Ei vielä kartoitettu | PENDING |  |
| `dexscreener_api.py` | `src/apis/dexscreener/` endpoint behavior | TESTED | New independent validated wrappers for token pools, token batches, pair lookup, search, token orders and supported chains; batch capped at 30 and path segments validated. Tests `test_dexscreener_api.py`; full validation `45254436959`.  |
| `discovery/__init__.py` | Ei vielä kartoitettu | PENDING |  |
| `discovery/birdeye_scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `discovery/dexscreener_scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `discovery/gecko_scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `discovery/pumpfun_scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `discovery/screenerbot_sources.py` | Ei vielä kartoitettu | PENDING |  |
| `discovery/token_aggregator.py` | Ei vielä kartoitettu | PENDING |  |
| `docs/DISCOVERY_QUALITY.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/EXECUTION_READINESS_PLAN.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/NATIVE_EXECUTION.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/OHLCV.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/PUMPSWAP_V2_BUILDER.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/RAYDIUM_CPMM_BUILDER.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/SCREENERBOT_PARITY_LEDGER.md` | ScreenerBot full-tree inventory and subsystem source references | UPDATED | Updated file-level parity findings for APIs, pools/cache, OHLCV, strategies, DCA, copy-wallet replay and swap error taxonomy. Commit `f2bd7a0`; validation `45254436959`. Full inventory audit remains open. |
| `docs/playground-token2022-share.txt` | Ei vielä kartoitettu | PENDING |  |
| `docs/token2022-signer.html` | Ei vielä kartoitettu | PENDING |  |
| `entry_intelligence.py` | Ei vielä kartoitettu | PENDING |  |
| `exit_policy.py` | Ei vielä kartoitettu | PENDING |  |
| `filter_pipeline.py` | Ei vielä kartoitettu | PENDING |  |
| `fluxbeam_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `indicators.py` | `src/ohlcvs/` and indicator consumers | TESTED | Aligned supported frames to `1m, 5m, 15m, 1h, 4h, 12h, 1d`; persisted-history validation preserves original malformed counters. Tests `test_ohlcv_indicators.py`; full validation `45254436959`.  |
| `liquidity_monitor.py` | Ei vielä kartoitettu | PENDING |  |
| `live_execution.py` | Ei vielä kartoitettu | PENDING |  |
| `live_trading.py` | Ei vielä kartoitettu | PENDING |  |
| `main.py` | `src/trader/` supervisor/position-monitor files (exact counterpart mapping pending) | PARTIAL | Position monitor and market-price timestamp handoff compared; DCA timestamp is fail-closed. Full file audit remains open. |
| `market_data.py` | Ei vielä kartoitettu | PENDING |  |
| `meteora_damm_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `meteora_damm_v2_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `meteora_dbc_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `meteora_dbc_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `meteora_dlmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `meteora_dlmm_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `moonit_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `native_execution.py` | Ei vielä kartoitettu | PENDING |  |
| `ohlcv_integrity.py` | Ei vielä kartoitettu | PENDING |  |
| `onchain_stream.py` | Ei vielä kartoitettu | PENDING |  |
| `orca_whirlpool_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `orca_whirlpool_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `paper_fill_model.py` | Ei vielä kartoitettu | PENDING |  |
| `paper_trading.py` | `src/trader/evaluators/dca.rs`, `src/trader/evaluators/exit.rs`, `src/positions/operations/dca.rs` | PARTIAL | DCA gate now requires fresh provider timestamp, cooldown/min-hold, first-entry/average basis, bounded size and in-flight guard; atomic add/partial close reviewed. Full position/exit lifecycle, daily-loss DCA gate and live transaction reconciliation remain open. Full validation `45254436959`.
| `playground_token2022_ata.ts` | Ei vielä kartoitettu | PENDING |  |
| `pool_account_fetcher.py` | Ei vielä kartoitettu | PENDING |  |
| `pool_analysis.py` | Ei vielä kartoitettu | PENDING |  |
| `pool_registry.py` | Ei vielä kartoitettu | PENDING |  |
| `pool_selection.py` | Ei vielä kartoitettu | PENDING |  |
| `position_policy.py` | `src/trader/policy.rs`, `src/trader/evaluators/dca.rs` | TESTED | Pure DCA evaluator covers cooldown, min hold, basis selection, fresh-price gate, hard stop and first-buy multiplier while retaining legacy `add_usd`; tests `test_position_policy.py`; full validation `45254436959` (998 tests).
| `protocol_quotes.py` | Ei vielä kartoitettu | PENDING |  |
| `protocol_validation.py` | Ei vielä kartoitettu | PENDING |  |
| `pumpfun_legacy_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `pumpswap_v2_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `raydium_amm_v4_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `raydium_clmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `raydium_clmm_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `raydium_cpmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `requirements.txt` | `tools/fonts/requirements.txt` | PENDING |  |
| `risk_manager.py` | `src/trader/safety/`, `src/trader/` risk gates | PARTIAL | DCA add capped by existing max ticket, balance fraction and minimum ticket; complete risk subsystem comparison remains open. |
| `route_comparison.py` | `src/swaps/router.rs`, `registry.rs`, `operations.rs` | TESTED | Read-only Jupiter/Raptor/direct-pool quote comparison now exposes typed failure kind, retryability, retry-after and HTTP status; execution remains locked. Tests `test_route_comparison.py`, `test_quote_errors.py`; full validation `45254436959`.  |
| `rpc_transport.py` | Ei vielä kartoitettu | PENDING |  |
| `rugcheck.py` | Ei vielä kartoitettu | PENDING |  |
| `scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `service_health.py` | Ei vielä kartoitettu | PENDING |  |
| `sniper_scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `solana_onchain.py` | Ei vielä kartoitettu | PENDING |  |
| `strategy.py` | Ei vielä kartoitettu | PENDING |  |
| `strategy_catalog.py` | `src/strategies/conditions/catalog.rs`, strategy policy | TESTED | Exposes per-condition timeframe schema and maximum concurrent evaluation policy; full validation `45254436959`.  |
| `strategy_conditions.py` | `src/strategies/conditions/` | TESTED | Added per-condition timeframe selection over seven OHLCV frames; selected timeframe overrides top-level 1m convenience metrics; missing/stale frames fail closed. Tests `test_strategy_conditions.py`; full validation `45254436959`.  |
| `strategy_rules.py` | `src/strategies/engine.rs`, `src/strategies/conditions/` | TESTED | Added bounded concurrent rule evaluation, deadline includes semaphore wait; saturation fails closed. Tests `test_strategy_rules.py`; full validation `45254436959`.  |
| `swap_receipt.py` | Ei vielä kartoitettu | PENDING |  |
| `telegram_alerts.py` | Ei vielä kartoitettu | PENDING |  |
| `test_discovery.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_advanced_security_validation.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_api_client_stats.py` | `src/apis/` client tests | TESTED | Covers per-host/per-endpoint request, cache, latency and failure telemetry; full validation `45254436959`. |
| `tests/test_ata_manager.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_ata_manager_api.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_blacklist.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_candidate_metadata_merge.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_candidate_observer.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_candidate_quality.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_candidate_quality_pipeline.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_copy_event_parser.py` | `src/wallets/watch/runtime.rs`, `types.rs` tests | TESTED | Covers exact wallet deltas, rent correction, wrapped SOL, loaded addresses and ambiguous/missing metadata; full validation `45254436959`.  |
| `tests/test_copy_freshness.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_copy_performance_protocol_telemetry.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_copy_risk.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_copy_trade_subscriptions.py` | `src/wallets/watch/` poller tests | TESTED | Covers wallet subscription completeness, bounded pagination, durable cursor replay, null-index retry, skip cap and stale-event suppression; full validation `45254436959`.  |
| `tests/test_copy_wallet_watchlist.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_core_parity_learning.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_database_backup.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_database_dashboard_api.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_database_ohlcv_integrity.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_database_pool_registry.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_dex_engine.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_dex_execution.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_dexscreener_api.py` | DexScreener API behavior tests | TESTED | Covers token pools, batch lookup, pair lookup, search, token orders and supported chains; full validation `45254436959`. |
| `tests/test_dexscreener_discovery.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_dexscreener_discovery_config.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_dexscreener_rate_buckets.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_discovery_mint_normalization.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_discovery_provenance.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_discovery_source_failures.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_discovery_source_health.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_entry_intelligence.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_exit_policy.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_expanded_rugcheck_risk.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_extended_market_filter_bounds.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_filter_pipeline.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_fluxbeam_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_fluxbeam_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_force_stop_dashboard_api.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_gecko_market_data.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_gecko_pool_discovery.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_gecko_recently_updated.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_indicator_feed_cache.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_jupiter_route_intelligence.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_meteora_damm_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_meteora_damm_v2_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_meteora_dbc_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_meteora_dbc_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_meteora_dlmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_meteora_dlmm_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_moonit_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_moonit_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_multi_window_market_filtering.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_native_dashboard_api.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_native_execution.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_new_persistence.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_ohlcv_indicators.py` | `src/ohlcvs/` tests | TESTED | Verifies seven reference timeframes and persisted-history readiness/quality behavior; full validation `45254436959`.  |
| `tests/test_ohlcv_integrity.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_onchain_indicators.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_orca_whirlpool_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_orca_whirlpool_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_paper_fill_model.py` | `tests/trader_dca.rs`, `src/positions/operations/` | PARTIAL | DCA integration fixture now supplies fresh provider timestamp, enabled policy/step and bounded paper balance; conservative fill-price assertion remains tested. Full validation `45254436959`; other fill-model cases need individual review. |
| `tests/test_paper_position_input_hardening.py` | `src/positions/` safety and policy tests | PARTIAL | Updated DCA policy-failure fixture for fail-closed policy snapshot; full validation `45254436959`. Remaining paper position tests still need individual review. |
| `tests/test_pool_account_fetcher.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_analysis.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_cache_prune.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_metric_filtering.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_refresh_priority.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_refresh_runtime_metrics.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_registry.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_selection.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_snapshot_integrity.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pool_watch_parity.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_position_management.py` | `tests/trader_dca.rs`, position database tests | PARTIAL | Atomic DCA/partial/full close accounting test reviewed; broader lifecycle parity remains open. |
| `tests/test_pre_strategy_security.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpfun_legacy_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpfun_legacy_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpportal_trade_stream.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpswap_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpswap_v2_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_amm_v4_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_amm_v4_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_clmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_clmm_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_cpmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_cpmm_quotes.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_risk_guard_extensions.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_route_comparison.py` | `src/swaps/` quote/route tests | TESTED | Quote comparison now verifies typed rejection status; full validation `45254436959`.  |
| `tests/test_rpc_transport.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_rugcheck_cache_concurrency.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_rugcheck_distribution_extensions.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_rugcheck_fee_lp_provider_gates.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_rugcheck_feed_parity.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_rugcheck_holder_lp_controls.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_rugcheck_report_propagation.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_safety_and_market_data.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_screenerbot_discovery_sources.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_screenerbot_parity_foundation.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_screenerbot_rugcheck_defaults.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_standard_indicator_math.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_strategy_conditions.py` | `src/strategies/conditions/` tests | TESTED | Covers selected timeframe metrics, missing timeframe fail-closed behavior and schema validation; full validation `45254436959`.  |
| `tests/test_strategy_quality_integration.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_strategy_rules.py` | `src/strategies/engine.rs` tests | TESTED | Covers concurrent-evaluation saturation and timeout fail-closed behavior; full validation `45254436959`.  |
| `tests/test_swap_receipt.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_token2022_inspector.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_token2022_inspector_api.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_token_aggregator.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_trading_control.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_wallet_preflight.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_wallet_preflight_api.py` | Ei vielä kartoitettu | PENDING |  |
| `token2022_inspector.py` | Ei vielä kartoitettu | PENDING |  |
| `token_security.py` | Ei vielä kartoitettu | PENDING |  |
| `trading_control.py` | Ei vielä kartoitettu | PENDING |  |
| `trading_manager.py` | Ei vielä kartoitettu | PENDING |  |
| `wallet_preflight.py` | Ei vielä kartoitettu | PENDING |  |

| `quote_errors.py` | `src/swaps/error.rs` | TESTED | Independent typed quote failure classifier for timeout, rate limit, no route, not tradable, rejected quote and unavailable; retry policy/HTTP status exposed. Tests `test_quote_errors.py`; full validation `45254436959`. |
| `tests/test_quote_errors.py` | `src/swaps/error.rs` behavior tests | TESTED | Covers timeout, rate limit/retry-after, no-route vs not-tradable distinction, rejected payload and provider 5xx; full validation `45254436959`. |
| `docs/TROJAN_FILE_AUDIT_INVENTORY.md` | Self-inventory | PARTIAL | Inventory generated from repository tree; this row records its own tracking file. |
| `docs/SCREENERBOT_FILE_COVERAGE.md` | ScreenerBot full-tree inventory | PARTIAL | Cross-repository file coverage inventory; per-file dispositions remain pending. |
| `tests/test_position_policy.py` | `src/trader/evaluators/dca.rs`, `tests/trader_dca.rs` | TESTED | Pure DCA gate tests cover legacy and multiplier sizing, price freshness, min-hold, cooldown, basis and hard-stop failures; full validation `45254436959` (998 tests).