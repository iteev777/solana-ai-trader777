# Trojan V2 — tiedostokohtainen auditointi

- Trojan-haara: `feature/dex-jupiter-noncustodial-execution-current`
- Vertailurepositorio: `farfary/ScreenerBot` @ `9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1`
- Inventaarion muodostus: 2026-11-23
- Trojanin seurattavat tiedostot: 239
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
| `ata_close_manager.py` | `src/chains/solana/assets/ata/close.rs`, `helpers.rs`, `wsol_sweep.rs` | PARTIAL | New non-custodial close implementation: validates canonical empty legacy/Token-2022 ATA, close authority, withheld transfer fees and rent refund; compiles/simulates unsigned CloseAccount order. Manual WSOL ATA wrapper only; no automatic idle scheduler or batch cleanup. Commits `d805391`, `15faa91`; tests `8979ab3`, `5908254`; dashboard route `9319a62`; API tests `6956c68`. |\n| `benchmarks/pool_rpc_parity.py` | No direct ScreenerBot benchmark file | NO-CHANGE | Read-only benchmark script compares the same live Solana RPC account-batch workload with Trojan pool refresh. No wallet key, signing, transaction or trading path. Reference tree has no matching benchmark file. Not executed during audit because it performs live mainnet RPC requests and writes local benchmark artifacts. |
| `benchmarks/rust_rpc/Cargo.toml` | `Cargo.toml` | PENDING |  |
| `benchmarks/rust_rpc/README.md` | `README.md`<br>`electron/README.md`<br>`electron/assets/README.md`<br>`src/apis/README.md`<br>`src/apis/llm/deepseek/README.md`<br>`src/ohlcvs/README.md`<br>`src/tokens/README.md` | PENDING |  |
| `benchmarks/rust_rpc/sidecar_smoke.py` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/src/bin/rpc_sidecar.rs` | Ei vielä kartoitettu | PENDING |  |
| `benchmarks/rust_rpc/src/main.rs` | `src/main.rs` | PENDING |  |
| `blacklist.py` | `src/tokens/database/blacklist.rs`, `src/trader/safety/blacklist.rs`, `src/pools/database/blacklist.rs` | PARTIAL | Full module and tests read. Token/pool/account denylist with base58 validation, soft removal and batch matching. Reference has token source precedence/user-cleared suppression, emergency position exit and TTL/failure-count pool/account quarantine; these behaviors are not equivalent. Dashboard only exposes manual CRUD today. |
| `candidate_observer.py` | No direct PumpPortal equivalent found in ScreenerBot; adjacent observation concepts in copy wallet watch | PARTIAL | Full module and focused tests read. Optional PumpPortal-only time-window observer; metered subscription opt-in, bounded sessions/events, signature dedupe, buyer concentration/volume/price gates and cleanup. No PumpPortal source found in reference; Trojan-specific feature, not a missing ScreenerBot feature. Concurrency/connection readiness and close-window race remain open. |
| `candidate_quality.py` | No direct candidate-ranking equivalent found; adjacent `src/filtering/engine.rs`, `src/filtering/stage.rs` are hard filter pipeline, not score ranking | PARTIAL | Full module and tests read. Independent prioritization score uses liquidity, multi-window volume/flow, source-family diversity, age and momentum; missing data remains unknown and gating disabled. No one-to-one reference equivalent; filter pipeline still needs separate comparison. |
| `clmm_quote_math.py` | `src/chains/solana/swaps/direct/venues/clmm_ticks.rs`, `src/chains/solana/swaps/direct/math.rs` | UPDATED | Full file compared. Added u64/u128/u256 bounds, strict integer/direction/fee validation, unique initialized-tick checks, Q64.64 scaled-liquidity guard, u64 output/fee saturation and current tick/sqrt-price consistency. Commits `ebc88d3`, `789e822`. |
| `copy_event_parser.py` | `src/chains/solana/transactions/deltas.rs`, `src/transactions/deltas.rs` | TESTED | Verrattu wallet-kohtaiset token/native-deltat, rent-korjaus, wSOL, loaded address -indeksit ja 50 000 lamportin kohinaraja. Säilyttää copy-luokittelun yhden päätokenin eventtinä ja liittää täydellisen subject asset delta -listan `transaction_deltas.py`:stä. Copy-eventin ja delta-ledgerin atominen tallennus testattu; full validation `45256464329` (1 020 testiä). |
| `copy_freshness.py` | `src/trader/copy/guards.rs`, `src/trader/copy/analytics.rs`, `src/wallets/watch/poller.rs` | TESTED | Measures chain-to-observation arrival age from captured receipt time, rejects future/invalid timestamps and stale recovered events. Tests `f9b9445`, `17c003f`; full validation `45254436959` (998 tests). |
| `copy_risk.py` | `src/trader/copy/risk.rs`, `src/trader/copy/sizing.rs` | PARTIAL | Full file and focused tests read. Trojan enforces wallet-isolated USD exposure/daily loss/position limits; reference enforces per-task target min/max, filter/self-copy, slippage, buy-once and SOL budget/sizing caps. These are complementary, not equivalent; task-level controls remain open. |
| `copy_trade.py` | `src/wallets/watch/service.rs`, `src/wallets/watch/poller.rs`, `src/wallets/watch/dedupe.rs`, `src/trader/copy/service.rs`, `src/trader/copy/guards.rs` | TESTED | Full monitor read. Added bounded paged catch-up, durable per-wallet cursor, oldest-first replay, safe null-index retry, replay dedupe, bounded skip-to-head, stale-event suppression and cursor retry when the in-memory paper queue is full. Tests cover cursor persistence, queue backpressure, null-index retry, page order and skip cap; full validation `45256464329`. A crash after queue acceptance but before processing still needs a durable outbox. |
| `dashboard.py` | `src/chains/solana/assets/ata/close.rs`, `helpers.rs` (route only) | PARTIAL | Added authenticated `/api/ata-close-order` route with required action header; dashboard file as a whole remains unaudited. Commit `9319a62`; API tests `6956c68`. |
| `database.py` | `src/database/`, `src/positions/db.rs`, `src/transactions/deltas.rs` | PARTIAL | Atomic paper DCA/partial-close paths inspected; schema v11 atomically persists all subject asset deltas with wallet events, validates subject/signature identity and preserves large raw integers. Tests cover rollback, idempotency and querying; full database parity audit remains open. |
| `devnet_e2e.py` | Ei vielä kartoitettu | PENDING |  |
| `devnet_token2022_e2e.py` | Ei vielä kartoitettu | PENDING |  |
| `devnet_token2022_signed_e2e.py` | Ei vielä kartoitettu | PENDING |  |
| `dex_capabilities.py` | Ei vielä kartoitettu | PENDING |  |
| `dex_engine.py` | Ei vielä kartoitettu | PENDING |  |
| `dex_execution.py` | Ei vielä kartoitettu | PENDING |  |
| `dexscreener_api.py` | `src/apis/dexscreener/` endpoint behavior | TESTED | New independent validated wrappers for token pools, token batches, pair lookup, search, token orders and supported chains; batch capped at 30 and path segments validated. Tests `test_dexscreener_api.py`; full validation `45254436959`.  |
| `discovery/__init__.py` | Python package marker | NO-CHANGE | 19 tavun pakettikuvaus luettu; ei sisällä suoritettavaa logiikkaa eikä vaadi vastaavaa Rust-lähdetiedostoa. |
| `discovery/birdeye_scanner.py` | Birdeye source behavior; ScreenerBot discovery source set | PARTIAL | Koko tiedosto luettu: V3 token-list, 400/404/422 trending fallback, 1h quota cooldown, response-shape/duplicate checks ja virheen propagointi. Birdeye on Trojanin lisälähde eikä ScreenerBotin yksi-yhteen discovery-vastine. Provider-family konsensus pidetään Birdeye-yhtenä. |
| `discovery/dexscreener_scanner.py` | `src/tokens/discovery_sources.rs` DexScreener profile/latest/top boost feeds | PARTIAL | Kolme feediä erillisinä tehtävinä ja endpoint-kohtaisina lähteinä; chain filtering, dedupe ja virheiden propagointi aggregaattorin health telemetryyn. Täysi payload- ja rate-limit-rajatapausvertailu vielä kesken. |
| `discovery/gecko_scanner.py` | `src/tokens/discovery_sources.rs` Gecko new/trending/recently-updated | PARTIAL | Koko tiedosto luettu: base+quote-token pool candidates, Gecko pool normalization, base-only market-data attribution, recently-updated Solana network filter ja metadata. Reference's deferred rate-limit response (skip current tick/retry next tick) vs Trojan's source failure semantics needs full review. |
| `discovery/pumpfun_scanner.py` | Birdeye meme listing; no direct ScreenerBot Pump.fun feed | PARTIAL | Tiedosto käyttää todellisuudessa Birdeye `new_listing` + meme_platform_enabled -endpointia ja trending fallbackia, ei Pump.fun API:a. Korjattu aggregaattorin source/provider attribution Birdeye-perheeseen ja lisätty canonical `scan_birdeye_meme_listing` alias; vanha import säilytetty. Commitit `c0c97f2`, `84b8eb9`, `47afd49`; testi `b64048f`. |
| `discovery/screenerbot_sources.py` | `src/tokens/discovery_sources.rs` RugCheck/Jupiter/CoinGecko/DeFiLlama feeds | PARTIAL | Yhdennetty lähdemoduuli sisältää RugCheck new/recent/trending/verified, Jupiter recent/organic/traded/trending, CoinGecko Solana contract ja DeFiLlama Solana protocol feeds; base58-mint validation, safe fetch/cache ja source-level errors. Täysi endpoint-parametrien, rate limitin ja fallbackien vertailu avoin. |
| `discovery/token_aggregator.py` | `src/tokens/discovery.rs` discovery runner and aggregation | PARTIAL | 23k tiedosto: provider/per-feed enablement, concurrent fetch, source health persistence, provider-family consensus, candidate merge/normalization/provenance, dedupe and DB persistence inspected in part. Reference shutdown-aware scheduler, skip-on-network-outage/contention and chain-adapter feed registration require complete comparison. Birdeye meme listing corrected to same provider family as Birdeye token list. |
| `docs/DISCOVERY_QUALITY.md` | `src/tokens/discovery.rs` provider-family consensus and source configuration | UPDATED | Korjattu dokumentaatio: `pumpfun_scanner.py` käyttää Birdeye meme listing -API:a ja kuuluu Birdeye provider familyyn; dokumentoitu uusi `TROJAN_BIRDEYE_MEME_LISTING_ENABLED` ja vanhan `TROJAN_PUMPFUN_ENABLED` -aliasmuuttujan yhteensopivuus. Commit `1f60f60`. |
| `docs/EXECUTION_READINESS_PLAN.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/NATIVE_EXECUTION.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/OHLCV.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/PUMPSWAP_V2_BUILDER.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/RAYDIUM_CPMM_BUILDER.md` | Ei vielä kartoitettu | PENDING |  |
| `docs/SCREENERBOT_PARITY_LEDGER.md` | ScreenerBot full-tree inventory and subsystem source references | UPDATED | Updated file-level parity findings for APIs, pools/cache, OHLCV, strategies, DCA, copy-wallet replay and swap error taxonomy. Commit `f2bd7a0`; validation `45254436959`. Full inventory audit remains open. |
| `docs/playground-token2022-share.txt` | Ei vielä kartoitettu | PENDING |  |
| `docs/token2022-signer.html` | Ei vielä kartoitettu | PENDING |  |
| `entry_intelligence.py` | Ei vielä kartoitettu | PENDING |  |
| `exit_policy.py` | `src/trader/evaluators/exit_rules.rs`, `exit_stop_loss.rs`, `exit_trailing.rs`, `exit_break_even.rs`, `exit_take_profit.rs`, `exit_time.rs`, `exit_time_stagnation.rs` | PARTIAL | Koko moduuli ja `tests/test_exit_policy.py` luettu. Trojan vastaa stop-loss/trailing/break-even/take-profit/time-loss/stagnation/max-hold -säännöistä, mutta ei vielä reference-tyyppistä exit-prioriteettikoordinaatiota, emergency safety exit -erottelua, LLM/strategy-exit -arviointia tai stop-loss partial/min-hold -asetuksia. Löydettiin take-profit-ladderin semanttinen ero: Trojanin `sell_fraction` on jäljellä olevasta positiosta, ScreenerBotin `sell_pct` alkuperäisestä positiosta. Lisätty yhteensopiva `sell_pct`-syöte ja lopullisen 100 % jäljellä olevan osuuden täysi sulku; CI-testit odottavat. |
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
| `paper_trading.py` | `src/trader/evaluators/dca.rs`, `src/trader/evaluators/exit.rs`, `src/trader/evaluators/exit_rules.rs`, `src/positions/operations/dca.rs`, `src/positions/operations/exit_ladder.rs` | PARTIAL | DCA paper add käyttää fresh-price/cooldown/min-hold/basis/size/in-flight -gateja ja atomic database-operaatiota. Exit ladder normalisoi alkuperäisen position osuudet; viimeinen 100 % jäljellä oleva osuus käyttää atomic full close -polkua. Koodimuutos commit `8146985`; täyden polun testi `c7e5161`; 1 029 testiä hyväksytty (run `45257483040`) ja full validation onnistui (run `45257482979`). Täysi lifecycle-, safety-exit-, live reconciliation- ja copy-position-omistajuuspariteetti avoin. |
| `playground_token2022_ata.ts` | Ei vielä kartoitettu | PENDING |  |
| `pool_account_fetcher.py` | Ei vielä kartoitettu | PENDING |  |
| `pool_analysis.py` | Ei vielä kartoitettu | PENDING |  |
| `pool_registry.py` | Ei vielä kartoitettu | PENDING |  |
| `pool_selection.py` | Ei vielä kartoitettu | PENDING |  |
| `position_policy.py` | `src/trader/policy.rs`, `src/trader/evaluators/dca.rs`, `src/trader/evaluators/exit_take_profit.rs` | PARTIAL | DCA evaluator vastaa cooldown/min-hold/freshness/basis/hard-stop/first-buy sizing -portteihin, säilyttää legacy `add_usd`, estää DCA:n kun policy on pois päältä, soveltaa min-holdin vain ensimmäiseen lisäykseen ja käsittelee täsmälleen DCA-drop-rajan floating-point toleranssilla. Exit ladder hyväksyy legacy `sell_fraction` ja reference-tyylisen `sell_pct` -pohjan. Commitit `ecca597`, `a4704af`, `9980999`; testit `e629bdd`, `0646aa3`; validointi odottaa. |
| `protocol_quotes.py` | Ei vielä kartoitettu | PENDING |  |
| `protocol_validation.py` | Ei vielä kartoitettu | PENDING |  |
| `pumpfun_legacy_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `pumpswap_v2_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `raydium_amm_v4_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `raydium_clmm_builder.py` | `src/chains/solana/swaps/direct/venues/raydium_clmm.rs`, direct swap instruction builder | UPDATED | Full unsigned builder read. Fixed swap_v2 fixed account list to always pass canonical Token and Token-2022 program IDs in required order, while still validating actual mint owners independently. Commit `67191ab`. Signing/broadcast remain disabled by design. |
| `raydium_clmm_quote.py` | `src/chains/solana/swaps/direct/venues/raydium_clmm.rs`, `src/chains/solana/pools/layouts/raydium_clmm.rs` | UPDATED | Full adapter read and reference venue comparison. Requires positive confirmed slot, finite snapshot age and refuses output equal to or greater than vault reserve. Commit `8c69f73`. Price-impact semantics remain different and documented. |
| `raydium_cpmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `requirements.txt` | `tools/fonts/requirements.txt` | PENDING |  |
| `risk_manager.py` | `src/trader/safety/loss_limit.rs`, `limits.rs`, `risk.rs`, `blacklist.rs`, `cooldown.rs` | PARTIAL | Koko moduuli ja `tests/test_risk_guard_extensions.py` luettu; vertailtu loss-limitin, position capin, emergency loss exitin, blacklist-exitin ja re-entry cooldownin vastuita. Trojanilla USD-denominoitu rolling realized-loss gate ja token-kohtainen cooldown entryssä; ScreenerBotin loss limit on erillinen entry-paussi, joka synkronoituu position-bookeista ja säilyttää manuaalisen resume-tilan. ScreenerBotin emergency >90% loss exit ja ownership/book-aware position capacity eivät ole yksi yhteen Trojanin entry-risk-managerin vastuulla; niiden exit- ja position-lifecycle-vastineiden auditointi jatkuu. DCA-addin ticket-, balance-fraction- ja minimum-ticket-rajat on tarkastettu. Ei koodimuutosta ennen kuin exit- ja positionhallinnan kokonaisvastuut on kartoitettu. |
| `route_comparison.py` | `src/swaps/router.rs`, `registry.rs`, `operations.rs` | TESTED | Read-only Jupiter/Raptor/direct-pool quote comparison now exposes typed failure kind, retryability, retry-after and HTTP status; execution remains locked. Tests `test_route_comparison.py`, `test_quote_errors.py`; full validation `45254436959`.  |
| `rpc_transport.py` | Ei vielä kartoitettu | PENDING |  |
| `rugcheck.py` | Ei vielä kartoitettu | PENDING |  |
| `scanner.py` | Ei vielä kartoitettu | PENDING |  |
| `service_health.py` | `src/services/health.rs`, `src/services/startup.rs`, `src/global/state.rs` | TESTED | Koko readiness-report moduuli luettu. Trojan aggregoi heartbeat-, database-, RPC-, market-data-, Telegram- ja copy-monitor -tarkistukset ja pitää live readinessin lukittuna. Reference tarjoaa lisäksi per-service typed health states ja startup-duration trackerin; ne jäävät service manager -gapiksi. Korjattu tulevan/virheellisen heartbeat-aikaleiman hyväksyntä; commit `1c67eba`. Testit `tests/test_service_health.py` (5 tapausta); 1 056 testiä läpäisi (run `45258652977`), full validation onnistui (run `45258652675`). |
| `sniper_scanner.py` | `src/tokens/discovery_sources.rs` (new-token feed responsibility only); no PumpPortal trade stream equivalent | PARTIAL | Full module and focused stream tests read. One shared PumpPortal WebSocket handles new-token and optional metered trade subscriptions, bounded queue/dedupe and reconnect resubscription. ScreenerBot uses different feed/runtime architecture; PumpPortal observer is Trojan-specific. |
| `solana_onchain.py` | Ei vielä kartoitettu | PENDING |  |
| `strategy.py` | Ei vielä kartoitettu | PENDING |  |
| `strategy_catalog.py` | `src/strategies/conditions/catalog.rs`, strategy policy | TESTED | Exposes per-condition timeframe schema and maximum concurrent evaluation policy; full validation `45254436959`.  |
| `strategy_conditions.py` | `src/strategies/conditions/` | TESTED | Added per-condition timeframe selection over seven OHLCV frames; selected timeframe overrides top-level 1m convenience metrics; missing/stale frames fail closed. Tests `test_strategy_conditions.py`; full validation `45254436959`.  |
| `strategy_rules.py` | `src/strategies/engine.rs`, `src/strategies/conditions/` | TESTED | Added bounded concurrent rule evaluation, deadline includes semaphore wait; saturation fails closed. Tests `test_strategy_rules.py`; full validation `45254436959`.  |
| `swap_receipt.py` | Ei vielä kartoitettu | PENDING |  |
| `telegram_alerts.py` | Ei vielä kartoitettu | PENDING |  |
| `test_discovery.py` | Trojanin discovery manual smoke script | NO-CHANGE | 478 tavun manuaalinen `collect_tokens()`-smoke script luettu; ei sisällä automaattisia assertioita eikä ScreenerBotissa ole vastaavaa Python CLI -tiedostoa. Varsinainen regressiotestaus on `tests/test_*discovery*.py`-tiedostoissa. |
| `tests/test_advanced_security_validation.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_api_client_stats.py` | `src/apis/` client tests | TESTED | Covers per-host/per-endpoint request, cache, latency and failure telemetry; full validation `45254436959`. |
| `tests/test_ata_manager.py` | `src/chains/solana/assets/ata/helpers.rs`, `close.rs`, `wsol_sweep.rs` | PARTIAL | Testit kattavat legacy SPL + Token-2022 ATA:n luonnin, immutable owner -tilin, transfer-fee-koon/rentin, transfer hook -eston, olemassa olevan ATA:n, riittämättömät varat ja virhetilanteita. Referenssin sulkemisen kilpailutilanteet, zero-balance retry ja WSOL sweep -idle-guard puuttuvat. |
| `tests/test_ata_close_manager.py` | `src/chains/solana/assets/ata/close.rs`, `helpers.rs`, `wsol_sweep.rs` | TESTED | Legacy + Token-2022 empty account, noncanonical ATA, nonzero balance, foreign close authority, withheld transfer fees, simulation validation and external-signing-only behavior. Full validation passed; unit suite 1045 tests. |\n| `tests/test_ata_manager_api.py` | `src/chains/solana/assets/ata/close.rs`, `helpers.rs` | TESTED | API tests cover action-header enforcement, unsigned ATA create/close order responses and invalid close/create rejection. Earlier full validation passed with 1 045 tests; current branch revalidation pending. |
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
| `tests/test_dexscreener_discovery.py` | `src/tokens/discovery_sources.rs` DexScreener feed behavior | PARTIAL | Testaa profiles/latest/top boosts erikseen, Solana chain filterin, dedupen, virheiden näkyvyyden, fallback wrapperin, provider-resulttien katkaisun sekä aggregator task registrationin. 1 022-testin aiempi run ja uudempi discovery-test run `45257922212` hyväksytty; koko tiedoston rivikohtainen auditointi avoin. |
| `tests/test_dexscreener_discovery_config.py` | `src/tokens/discovery.rs` per-feed config | PARTIAL | Testaa provider-level ja endpoint-level DexScreener enable/disable -kytkimet. Runtime config reload -pariteetti vielä avoin. |
| `tests/test_dexscreener_rate_buckets.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_discovery_mint_normalization.py` | `src/tokens/discovery.rs::normalize_mint` | PARTIAL | Testaa oikean Solana Pubkeyn pituuden, chainin, malformed base58in ja native/quote asset exclusionin. Reference käyttää chain adapter -validointia; Trojanin normalize_token käyttää Solana-spesifistä Pubkey-validointia. |
| `tests/test_discovery_provenance.py` | `src/tokens/discovery.rs` CandidateAggregate | PARTIAL | Testaa saman providerin useiden feedien laskemisen yhdeksi riippumattomaksi lähteeksi, eri providerien konsensuksen ja virheelliset market metrics -arvot. Birdeye meme listing -provider attribution korjattu. |
| `tests/test_discovery_source_failures.py` | `src/tokens/discovery.rs` per-source errors | PARTIAL | Testaa Gecko/Birdeye/meme-listing transport errors ja puuttuvat API-avaimet; virheet eivät saa muuttua onnistuneiksi tyhjiksi tuloksiksi. |
| `tests/test_discovery_source_health.py` | `src/tokens/discovery.rs` DiscoveryStats | PARTIAL | Testaa lähteen latency/success/failure history ja independent-provider ranking. Trojan persistoi health-statistiikan SQLiteen; reference käyttää per-run statsia ja typed deferred rate-limit skip -tilaa. |
| `tests/test_entry_intelligence.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_exit_policy.py` | `src/trader/evaluators/exit_rules.rs` and individual exit rule tests | PARTIAL | Testit luettu; stop-loss-prioriteetti, peak-based trailing, break-even, take-profit, stagnation, max-hold, invalid price/config ja trailing bands. Verrattava vielä koko referenssin edge-case testimatriisiin. |
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
| `tests/test_position_management.py` | `tests/trader_dca.rs`, `src/positions/operations/exit_ladder.rs` | PARTIAL | Atomic DCA/partial/full close accounting sekä final original-share exit-ladder tierin full-close reitti testattu; 1 029 testiä hyväksytty (run `45257483040`), full validation run `45257482979` hyväksytty. Laajempi lifecycle parity avoin. |
| `tests/test_pre_strategy_security.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpfun_legacy_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpfun_legacy_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpportal_trade_stream.py` | Trojan-specific PumpPortal stream tests; no direct reference tests | TESTED | Full test file read; trade-vs-create routing, reconnect resubscription, same-socket watch/unwatch and no-key fail-closed behavior covered. |
| `tests/test_pumpswap_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_pumpswap_v2_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_amm_v4_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_amm_v4_direct_quote.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_clmm_builder.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_raydium_clmm_direct_quote.py` | `src/chains/solana/swaps/direct/venues/clmm_ticks.rs` and `raydium_clmm.rs` tests | TESTED | Full 514-line suite read. Added integer-width, malformed/duplicate tick, output saturation, tick-price consistency, positive slot, vault reserve equality and fixed swap_v2 program-account assertions. Full validation passed on 1,045-test run. |
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
| `tests/test_service_health.py` | `src/services/health.rs`, `src/services/startup.rs` | TESTED | Viisi testiä: fresh/stale/future/missing/non-finite heartbeat ja täsmällinen 30 s staleness-raja; 1 056 testiä läpäisi (run `45258652977`), full validation `45258652675`. |
| `tests/test_safety_and_market_data.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_screenerbot_discovery_sources.py` | `src/tokens/discovery_sources.rs` | PARTIAL | Testaa RugCheck/Jupiter feed mergeä, erillisiä Jupiter endpointteja, invalid payload errors sekä CoinGecko/DeFiLlama Solana contract filters. Full parameter/rate-limit parity review avoin. |
| `tests/test_screenerbot_parity_foundation.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_screenerbot_rugcheck_defaults.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_standard_indicator_math.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_strategy_conditions.py` | `src/strategies/conditions/` tests | TESTED | Covers selected timeframe metrics, missing timeframe fail-closed behavior and schema validation; full validation `45254436959`.  |
| `tests/test_strategy_quality_integration.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_strategy_rules.py` | `src/strategies/engine.rs` tests | TESTED | Covers concurrent-evaluation saturation and timeout fail-closed behavior; full validation `45254436959`.  |
| `tests/test_swap_receipt.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_token2022_inspector.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_token2022_inspector_api.py` | Ei vielä kartoitettu | PENDING |  |
| `tests/test_token_aggregator.py` | `src/tokens/discovery.rs` | PARTIAL | Testaa source health persistence, per-feed enablement, concurrent merge, source provenance, data enrichment without overwriting good values and provider-family consensus. Birdeye meme feed now counts under Birdeye, not as a second provider. Unit test run `45257922212` passed. |
| `tests/test_trading_control.py` | `src/global/mod.rs` force-stop tests | TESTED | Kattaa reason/timestamp, whitespace + 300 merkin raja, resume cleanup, non-bool rejection ja risk admission halt/resume. 1 029 testiä hyväksytty ajossa `45257483040`; täysi validointi `45257482979`. |
| `tests/test_wallet_balances.py` | `src/chains/solana/accounts/balances.rs` tests | TESTED | Uudet testit tyhjälle syötteelle, järjestykselle, invalid-keylle, 50-koon erärajalle, tuntemattomalle saldolle, virheelliselle vastaukselle ja eräkohtaiselle RPC-virheelle. Commit `f71e667`; 1 022 unit testiä hyväksytty ajossa `45256897201`; täysi validointi `45256883273` hyväksytty. |
| `tests/test_wallet_preflight.py` | `src/chains/solana/accounts/balances.rs`, `src/chains/solana/assets/ata/balance.rs` | PARTIAL | Wallet preflight -testit luettu; testaa legacy-token-tilien omistajuuden, mintin, määrän, reserve-vaatimuksen ja yhden confirmed-snapshotin. Batch SOL-balance -ominaisuus katetaan erillisessä uudessa moduulissa. |
| `tests/test_wallet_preflight_api.py` | `src/chains/solana/accounts/balances.rs` | PENDING | API-reitin ja virhetilanteiden tiedostokohtainen tarkastus avoin. |
| `token_security.py` | Ei vielä kartoitettu | PENDING |  |
| `trading_control.py` | `src/global/mod.rs` force-stop state | TESTED | Koko 45-rivinen moduuli ja testit luettu. Process-local, lukittu force-stop flag + timestamp/operator reason; clear nollaa metadatan, syöte validoidaan ja reason rajataan 300 merkkiin. Reference käyttää SeqCst AtomicBool + RwLock, sama prosessielinkaari; Trojanin dashboard status lisää paper scope/live locked -tiedot. Tässä vastuussa ei havaittu puuttuvaa toimintaa; laajempi runtime readiness/restart kuuluu `service_health.py`/`main.py` auditointiin. Testit sisältyvät 1 029 testin hyväksyttyyn ajoon `45257483040`. |
| `trading_manager.py` | Ei vielä kartoitettu | PENDING |  |
| `wallet_balances.py` | `src/chains/solana/accounts/balances.rs` | NEW-IMPLEMENTATION | Lisätty 50 osoitteen erissä tapahtuva SOL-saldohaku; säilyttää järjestyksen, erottaa puuttuvan tilin (0 SOL) tuntemattomasta (None), hylkää virheelliset osoitteet ja rajaa RPC-virheen eräkohtaiseksi. Commit `b58a806`; testit `f71e667`; validointi odottaa. |
| `token2022_inspector.py` | `src/chains/solana/assets/ata/balance.rs`, `helpers.rs`, Token-2022 layout validation | PARTIAL | Token-2022 TLV-laajennusten tiukka parseri, tilin koko-/tyyppitarkistus ja riskilaajennusten swap-compatibility gates. ATA manager käyttää extension-aware tilikokoa. Täysi token-account holdings/frozen/withheld-fee accounting -pariteetti avoin. |
| `wallet_preflight.py` | `src/chains/solana/accounts/balances.rs`, `src/chains/solana/assets/ata/balance.rs`, `src/chains/solana/swaps/presend.rs` | PARTIAL | Koko 208-rivinen tiedosto ja testit luettu. Yhden confirmed getMultipleAccounts -snapshotin lompakko-, mintti- ja token-tilitarkastus; base58/u64/layout/omistajuus/frozen/reserve/ATA-tarkistus fail-closed. Rajattu legacy SPL -token-ohjelmaan nykyisten native builderien vuoksi; Token-2022 ei ole tämän moduulin kattama. Referenssin batch-saldohaku toteutettu erikseen `wallet_balances.py`:ssä. Tarkempi fee/rent/presend-gate-pariteetti avoin. |

| `transaction_deltas.py` | `src/chains/solana/transactions/deltas.rs`, `src/transactions/deltas.rs` | PARTIAL | Verrattu koko olennaiseen delta-extraction-logiikkaan: kaikki subject-owned mintit raw-yksiköissä, native SOL + non-wSOL token-tilien rent-korjaus, fee metadata, venue/kind, slot/block time/tx index ja fail-closed epäselville tiedoille. Trojan käyttää omaa Python-toteutusta ja säilyttää fee-metadatan myös silloin kun seurattu lompakko ei ole fee payer (kuten referenssi). Kytketty copy-eventin mukana tallennettavaksi, mutta yleisen transaktiohistorian ja backfillin vastaavuus avoin. |
| `tests/test_transaction_deltas.py` | `src/chains/solana/transactions/deltas.rs` tests | TESTED | Covers raw mint deltas, native SOL, fee payer attribution, ATA-rent correction, failed/ambiguous transactions and serialization; full validation `45256464329` (1,020 tests). |
| `docs/TROJAN_FILE_AUDIT_INVENTORY.md` | Self-inventory | PARTIAL | Inventory generated from repository tree; this row records its own tracking file. |
| `docs/SCREENERBOT_FILE_COVERAGE.md` | ScreenerBot full-tree inventory | PARTIAL | Cross-repository file coverage inventory; per-file dispositions remain pending. |
| `tests/test_position_policy.py` | `src/trader/evaluators/dca.rs`, `src/trader/evaluators/exit_take_profit.rs`, `tests/trader_dca.rs` | PARTIAL | Aiemmat DCA/exit-ladder testit sekä uudet testit DCA disabled -portille, ensimmäisen lisäyksen min-holdille ja tiukalle drop-rajan vertailulle. Uusien rajatapaustestien validointi odottaa. |
| `quote_errors.py` | `src/swaps/error.rs` | PARTIAL | Compared typed quote-error vocabulary and retry policy. Trojan distinguishes timeout/rate-limit/no-route/not-tradable/router rejection/unavailable, but some classes still depend on provider text and registry/no-router/not-offered/severity/permanent-verdict variants are absent. |
| `tests/test_quote_errors.py` | `src/swaps/error.rs` tests | TESTED | Covers timeout, rate-limit, no-route vs not-tradable, invalid quote, server failure and route-comparison propagation; included in 998-test validation. |
| `tests/test_token_decimals.py` | `src/tokens/decimals.rs` tests | TESTED | Tests cover cache hit, invalid decimals, RPC failure cache, invalid input, deterministic single-flight concurrency and cache status. Unit test run `45259186971` passed with 1 056 tests. |
| `token_decimals.py` | `src/tokens/decimals.rs` | PARTIAL | Bounded TTL cache and per-mint single-flight RPC lookup. Compared with reference: currently no DB preload/persist, data-server/RugCheck fallback, chain-scoped cache or transient-vs-permanent failure classification; no call sites found in inspected main/filter/market/pool/security modules. Added focused tests before integration. |

| `service_manager.py` | `src/services/mod.rs`, `src/services/global.rs`, `src/services/startup.rs`, `src/services/health.rs`, `src/services/startup_order_tests.rs`, `src/services/stop_service_tests.rs` | PARTIAL | Uusi itsenäinen Python service manager: deterministic dependency DAG, missing/cycle rejection, essential/non-essential startup failures, dependent blocking, reverse-order shutdown, safe single-service stop, per-service lifecycle timestamps/errors/restarts, health normalization/cache, metrics snapshot ja global singleton. Korjattu `str.join` collision: timed join vain thread-like handlelle jossa `is_alive()`. Commit `a14311a`, hardening `3d44412`/`2380ad5`, stop fix `5706fd0`. 1 080 testiä hyväksytty (run `45259909286`), full validation `45259895880`. Integrointi nykyiseen `main.py` worker-lifecycleen ja per-service poll/CPU/memory metrics ovat vielä avoinna. Reference käyttää async Tokio task/Notify/task monitor -mallia; Trojan käyttää callback/thread handleja eikä koskaan force-killaa Python threadia. |
| `tests/test_service_manager.py` | `src/services/startup_order_tests.rs`, `src/services/stop_service_tests.rs` | TESTED | 14+ lifecycle tests cover deterministic dependency ordering, cycles/missing/disabled dependencies, essential rollback, non-essential isolation, independent stop, dependent protection, thread timeout/no-force-kill, unexpected worker exit, health cache/error and global singleton. `str.join` collision fixed in `5706fd0`. 1 080 tests passed (run `45259909286`); full validation passed (run `45259895880`). |