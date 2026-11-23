# Trojan V2 — tiedostokohtainen auditointi

- Trojan-haara: `feature/dex-jupiter-noncustodial-execution-current`
- Vertailurepositorio: `farfary/ScreenerBot` @ `9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1`
- Inventaarion muodostus: 2026-11-23
- Trojanin seurattavat tiedostot: 232
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
| `ata_manager.py` | `src/chains/solana/assets/ata/helpers.rs`, `close.rs`, `balance.rs` | PARTIAL | Koko tiedosto ja testit luettu. Rakentaa ja simuloi unsigned CreateIdempotent-ATA-tilauksen, laskee extension-aware Token-2022 rentin, tarkistaa nykyisen ATA:n, walletin SOL-reserven, fee-estimaatin ja packet size -rajan. Ei allekirjoita eikä lähetä. Referenssin ATA-close/cleanup ja automaattinen WSOL sweep puuttuvat; niiden toteutus on suunniteltava non-custodial unsigned-order -malliin, ei kopioida palvelinpuolen allekirjoitusta. |
| `benchmarks/pool_rpc_parity.py` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/Cargo.toml` | `Cargo.toml` | PENDING |  |
| `benchmarks/rust_rpc/README.md` | `README.md`<br>`electron/README.md`<br>`electron/assets/README.md`<br>`src/apis/README.md`<br>`src/apis/llm/deepseek/README.md`<br>`src/ohlcvs/README.md`<br>`src/tokens/README.md` | PENDING |  |
| `benchmarks/rust_rpc/sidecar_smoke.py` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/src/bin/rpc_sidecar.rs` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/src/main.rs` | `src/main.rs` | PENDING |  |
| `blacklist.py` | `src/tokens/database/blacklist.rs`, `src/trader/safety/blacklist.rs`, `src/pools/database/blacklist.rs` | PARTIAL | Full module and tests read. Token/pool/account denylist with base58 validation, soft removal and batch matching. Reference has token source precedence/user-cleared suppression, emergency position exit and TTL/failure-count pool/account quarantine; these behaviors are not equivalent. Dashboard only exposes manual CRUD today. |
| `candidate_observer.py` | No direct PumpPortal equivalent found in ScreenerBot; adjacent observation concepts in copy wallet watch | PARTIAL | Full module and focused tests read. Optional PumpPortal-only time-window observer; metered subscription opt-in, bounded sessions/events, signature dedupe, buyer concentration/volume/price gates and cleanup. No PumpPortal source found in reference; Trojan-specific feature, not a missing ScreenerBot feature. Concurrency/connection readiness and close-window race remain open. |
| `candidate_quality.py` | No direct candidate-ranking equivalent found; adjacent `src/filtering/engine.rs`, `src/filtering/stage.rs` are hard filter pipeline, not score ranking | PARTIAL | Full module and tests read. Independent prioritization score uses liquidity, multi-window volume/flow, source-family diversity, age and momentum; missing data remains unknown and gating disabled. No one-to-one reference equivalent; filter pipeline still needs separate comparison. |
| `clmm_quote_math.py` | Ei vielä kartoitettu | PENDING |  |
| `copy_event_parser.py` | `src/chains/solana/transactions/deltas.rs`, `src/transactions/deltas.rs` | TESTED | Verrattu wallet-kohtaiset token/native-deltat, rent-korjaus, wSOL, loaded address -indeksit ja 50 000 lamportin kohinaraja. Säilyttää copy-luokittelun yhden päätokenin eventtinä ja liittää täydellisen subject asset delta -listan `transaction_deltas.py`:stä. Copy-eventin ja delta-ledgerin atominen tallennus testattu; full validation `45256464329` (1 020 testiä). |
| `copy_freshness.py` | `src/trader/copy/guards.rs`, `src/trader/copy/analytics.rs`, `src/wallets/watch/poller.rs` | TESTED | Measures chain-to-observation arrival age from captured receipt time, rejects future/invalid timestamps and stale recovered events. Tests `f9b9445`, `17c003f`; full validation `45254436959` (998 tests). |
| `copy_risk.py` | `src/trader/copy/risk.rs`, `src/trader/copy/sizing.rs` | PARTIAL | Full file and focused tests read. Trojan enforces wallet-isolated USD exposure/daily loss/position limits; reference enforces per-task target min/max, filter/self-copy, slippage, buy-once and SOL budget/sizing caps. These are complementary, not equivalent; task-level controls remain open. |
| `copy_trade.py` | `src/wallets/watch/service.rs`, `src/wallets/watch/poller.rs`, `src/wallets/watch/dedupe.rs`, `src/trader/copy/service.rs`, `src/trader/copy/guards.rs` | TESTED | Full monitor read. Added bounded paged catch-up, durable per-wallet cursor, oldest-first replay, safe null-index retry, replay dedupe, bounded skip-to-head, stale-event suppression and cursor retry when the in-memory paper queue is full. Tests cover cursor persistence, queue backpressure, null-index retry, page order and skip cap; full validation `45256464329`. A crash after queue acceptance but before processing still needs a durable outbox. |
| `dashboard.py` | Ei vielä kartoitettu | PENDING |  |
| `database.py` | `src/database/`, `src/positions/db.rs`, `src/transactions/deltas.rs` | PARTIAL | Atomic paper DCA/partial-close paths inspected; schema v11 atomically persists all subject asset deltas with wallet events, validates subject/signature identity and preserves large raw integers. Tests cover rollback, idempotency and querying; full database parity audit remains open. |
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
| `risk_manager.py` | `src/trader/safety/loss_limit.rs`, `limits.rs`, `risk.rs`, `blacklist.rs`, `cooldown.rs` | PARTIAL | Koko moduuli ja `tests/test_risk_guard_extensions.py` luettu; vertailtu loss-limitin, position capin, emergency loss exitin, blacklist-exitin ja re-entry cooldownin vastuita. Trojanilla USD-denominoitu rolling realized-loss gate ja token-kohtainen cooldown entryssä; ScreenerBotin loss limit on erillinen entry-paussi, joka synkronoituu position-bookeista ja säilyttää manuaalisen resume-tilan. ScreenerBotin emergency >90% loss exit ja ownership/book-aware position capacity eivät ole yksi yhteen Trojanin entry-risk-managerin vastuulla; niiden exit- ja position-lifecycle-vastineiden auditointi jatkuu. DCA-addin ticket-, balance-fraction- ja minimum-ticket-rajat on tarkastettu. Ei koodimuutosta ennen kuin exit- ja positionhallinnan kokonaisvastuut on kartoitettu. |
| `route_comparison.py` | `src/swaps/router.rs`, `registry.rs`, `operations.rs` | TESTED | Read-only Jupiter/Raptor/direct-pool quote comparison now exposes typed failure kind, retryability, retry-after and HTTP status; execution remains locked. Tests `test_route_comparison.py`, `test_quote_errors.py`; full validation `45254436959`.  |
| `rpc_transport.py` | Ei vielä kartoitettu | PENDING |  |
| `rugcheck.py` | Ei vielä kartoitettu | PENDING |  |
| `scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `service_health.py` | Ei vielä kartoitettu | PENDING |  |
| `sniper_scanner.py` | `src/tokens/discovery_sources.rs` (new-token feed responsibility only); no PumpPortal trade stream equivalent | PARTIAL | Full module and focused stream tests read. One shared PumpPortal WebSocket handles new-token and optional metered trade subscriptions, bounded queue/dedupe and reconnect resubscription. ScreenerBot uses different feed/runtime architecture; PumpPortal observer is Trojan-specific. |
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
| `tests/test_ata_manager.py` | `src/chains/solana/assets/ata/helpers.rs`, `close.rs`, `wsol_sweep.rs` | PARTIAL | Testit kattavat legacy SPL + Token-2022 ATA:n luonnin, immutable owner -tilin, transfer-fee-koon/rentin, transfer hook -eston, olemassa olevan ATA:n, riittämättömät varat ja virhetilanteita. Referenssin sulkemisen kilpailutilanteet, zero-balance retry ja WSOL sweep -idle-guard puuttuvat. |
| `tests/test_ata_manager_api.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_blacklist.py` | `src/tokens/database/blacklist_tests.rs`, pool blacklist tests | TESTED | Full test file read; add/list/match/soft-remove/reactivation, invalid key/type, account report matching and schema initialization covered. Broader reference semantics remain uncovered. |
| `tests/test_candidate_metadata_merge.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_candidate_observer.py` | Trojan-specific PumpPortal observer tests; no direct reference tests | TESTED | Full test file read; validation/dedupe, concentration, opt-in/API-key gate and shared WebSocket lifecycle covered. Full validation on `bbff47a` included 998 tests. |
| `tests/test_candidate_quality.py` | Trojan-specific ranking tests; adjacent filter tests | TESTED | Full test file read; source-family diversity, known market context and missing-data behavior covered. Ranking-vs-reference filter semantics remain separate. |
| `tests/test_candidate_quality_pipeline.py` | Trojan-specific ranking tests; adjacent filter profile tests | TESTED | Full test file read; hard rejects, missing-data posture, market enrichment and deterministic tier ordering covered. |
| `tests/test_copy_event_parser.py` | `src/chains/solana/transactions/deltas.rs`, `src/transactions/direction_tests.rs` | TESTED | Covers exact wallet deltas, ATA rent correction, wrapped SOL, loaded addresses, full asset-delta attachment and ambiguous/missing/mixed metadata; full validation `45256464329`. |
| `tests/test_copy_freshness.py` | `src/trader/copy/guards.rs` tests | TESTED | Tests arrival-time age, queue-delay independence, stale/future/invalid timestamps and bounded fallback; 998-test full validation passed on `bbff47a`. |
| `tests/test_copy_performance_protocol_telemetry.py` | `src/trader/copy/analytics_tests.rs`, protocol validation tests | PARTIAL | Full test file read; validates copy-wallet P&L attribution and open exposure plus protocol telemetry aggregation. Reference analytics/round ownership tests still require full comparison. |
| `tests/test_copy_risk.py` | `src/trader/copy/risk.rs`, `src/trader/copy/sizing.rs` tests | TESTED | Full test file read; wallet isolation, budget caps, cross-wallet mint ownership, position limit and daily-loss gates covered; reference task-level gates remain unimplemented. |
| `tests/test_copy_trade_subscriptions.py` | `src/wallets/watch/poller.rs`, `src/wallets/watch/dedupe.rs`, `src/wallets/watch/service.rs` | TESTED | Covers wallet subscription completeness, bounded pagination, durable cursor replay, null-index retry, skip cap and stale-event suppression; full validation `45254436959`. |
| `tests/test_copy_wallet_watchlist.py` | `src/wallets/watch/service_targets.rs`, `src/wallets/watch/database/` tests | TESTED | Full test file read; base58 validation, environment/dashboard merge, 20-wallet limit, re-enable and per-wallet cursor persistence covered; broader target reconciliation remains open. |
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
| `tests/test_pumpportal_trade_stream.py` | Trojan-specific PumpPortal stream tests; no direct reference tests | TESTED | Full test file read; trade-vs-create routing, reconnect resubscription, same-socket watch/unwatch and no-key fail-closed behavior covered. |
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
| `tests/test_wallet_balances.py` | `src/chains/solana/accounts/balances.rs` tests | TESTED | Uudet testit tyhjälle syötteelle, järjestykselle, invalid-keylle, 50-koon erärajalle, tuntemattomalle saldolle, virheelliselle vastaukselle ja eräkohtaiselle RPC-virheelle. Commit `f71e667`; 1 020 unit testiä hyväksytty ajossa `45256478628`; täysi validointi `45256464329` hyväksytty. |
| `tests/test_wallet_preflight.py` | `src/chains/solana/accounts/balances.rs`, `src/chains/solana/assets/ata/balance.rs` | PARTIAL | Wallet preflight -testit luettu; testaa legacy-token-tilien omistajuuden, mintin, määrän, reserve-vaatimuksen ja yhden confirmed-snapshotin. Batch SOL-balance -ominaisuus katetaan erillisessä uudessa moduulissa. |
| `tests/test_wallet_preflight_api.py` | `src/chains/solana/accounts/balances.rs` | PENDING | API-reitin ja virhetilanteiden tiedostokohtainen tarkastus avoin. |
| `token_security.py` | Ei vielä kartoitettu | PENDING |  |
| `trading_control.py` | Ei vielä kartoitettu | PENDING |  |
| `trading_manager.py` | Ei vielä kartoitettu | PENDING |  |
| `wallet_balances.py` | `src/chains/solana/accounts/balances.rs` | NEW-IMPLEMENTATION | Lisätty 50 osoitteen erissä tapahtuva SOL-saldohaku; säilyttää järjestyksen, erottaa puuttuvan tilin (0 SOL) tuntemattomasta (None), hylkää virheelliset osoitteet ja rajaa RPC-virheen eräkohtaiseksi. Commit `b58a806`; testit `f71e667`; validointi odottaa. |
| `token2022_inspector.py` | `src/chains/solana/assets/ata/balance.rs`, `helpers.rs`, Token-2022 layout validation | PARTIAL | Token-2022 TLV-laajennusten tiukka parseri, tilin koko-/tyyppitarkistus ja riskilaajennusten swap-compatibility gates. ATA manager käyttää extension-aware tilikokoa. Täysi token-account holdings/frozen/withheld-fee accounting -pariteetti avoin. |
| `wallet_preflight.py` | `src/chains/solana/accounts/balances.rs`, `src/chains/solana/assets/ata/balance.rs`, `src/chains/solana/swaps/presend.rs` | PARTIAL | Koko 208-rivinen tiedosto ja testit luettu. Yhden confirmed getMultipleAccounts -snapshotin lompakko-, mintti- ja token-tilitarkastus; base58/u64/layout/omistajuus/frozen/reserve/ATA-tarkistus fail-closed. Rajattu legacy SPL -token-ohjelmaan nykyisten native builderien vuoksi; Token-2022 ei ole tämän moduulin kattama. Referenssin batch-saldohaku toteutettu erikseen `wallet_balances.py`:ssä. Tarkempi fee/rent/presend-gate-pariteetti avoin. |

| `transaction_deltas.py` | `src/chains/solana/transactions/deltas.rs`, `src/transactions/deltas.rs` | PARTIAL | Verrattu koko olennaiseen delta-extraction-logiikkaan: kaikki subject-owned mintit raw-yksiköissä, native SOL + non-wSOL token-tilien rent-korjaus, fee metadata, venue/kind, slot/block time/tx index ja fail-closed epäselville tiedoille. Trojan käyttää omaa Python-toteutusta ja säilyttää fee-metadatan myös silloin kun seurattu lompakko ei ole fee payer (kuten referenssi). Kytketty copy-eventin mukana tallennettavaksi, mutta yleisen transaktiohistorian ja backfillin vastaavuus avoin. |
| `tests/test_transaction_deltas.py` | `src/chains/solana/transactions/deltas.rs` tests | TESTED | Covers raw mint deltas, native SOL, fee payer attribution, ATA-rent correction, failed/ambiguous transactions and serialization; full validation `45256464329` (1,020 tests). |
| `docs/TROJAN_FILE_AUDIT_INVENTORY.md` | Self-inventory | PARTIAL | Inventory generated from repository tree; this row records its own tracking file. |
| `docs/SCREENERBOT_FILE_COVERAGE.md` | ScreenerBot full-tree inventory | PARTIAL | Cross-repository file coverage inventory; per-file dispositions remain pending. |
| `tests/test_position_policy.py` | `src/trader/evaluators/dca.rs`, `tests/trader_dca.rs` | TESTED | Pure DCA gate tests cover legacy and multiplier sizing, price freshness, min-hold, cooldown, basis and hard-stop failures; full validation `45254436959` (998 tests).
| `quote_errors.py` | `src/swaps/error.rs` | PARTIAL | Compared typed quote-error vocabulary and retry policy. Trojan distinguishes timeout/rate-limit/no-route/not-tradable/router rejection/unavailable, but some classes still depend on provider text and registry/no-router/not-offered/severity/permanent-verdict variants are absent. |
| `tests/test_quote_errors.py` | `src/swaps/error.rs` tests | TESTED | Covers timeout, rate-limit, no-route vs not-tradable, invalid quote, server failure and route-comparison propagation; included in 998-test validation. |