# Trojan V2 and ScreenerBot comparison

The tracked-file inventory covers all 130 paths in Trojan at
[`f64ab51`](https://github.com/iteev777/solana-ai-trader777/tree/f64ab51009a69b764ba4f6e486d7a383e96ab9b9)
and all 3,043 paths in ScreenerBot at
[`9cc0b74`](https://github.com/farfary/ScreenerBot/tree/9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1f).
The [TSV inventory](screenerbot-file-inventory.tsv) maps paths in both directions
by function and identifies changed Trojan files. Counterpart prefixes denote
areas for comparison. Exact protocol mappings are included separately in the
inventory. Listing a path establishes inventory coverage. The detailed source
review concentrated on the groups in the table below.

ScreenerBot's Business Source License 1.1 was reviewed in the reference
checkout. Trojan's changes use its existing Python architecture and independent
implementations of the observed requirements.

| Area | ScreenerBot reference | Trojan result |
| --- | --- | --- |
| Solana protocols | `src/chains/solana/pools/{decoders,layouts}/`, `src/chains/solana/swaps/direct/venues/` | Existing read-only quote adapters cover Raydium AMM v4, CPMM and CLMM, Orca Whirlpool, Meteora DAMM, DBC and DLMM, PumpSwap, Pump.fun legacy, FluxBeam and Moonit. Direct-pool results are rechecked for freshness after parallel quote collection. Fixture tests exercise protocol arithmetic and decoder guards. |
| Quotes and costs | `src/swaps/operations.rs`, `src/chains/solana/swaps/routers/`, `src/chains/solana/swaps/cost_guard/` | Validates raw u64 inputs and output amounts, route identity, hop count, freshness and slippage before selection. Raptor route results use the provider's stated price-impact units. Fee and slippage caps remain active. |
| Discovery, market data and filtering | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs`, `src/apis/`, `src/filtering/` | Added GeckoTerminal recently updated Solana token metadata. Separates valid empty results from source failures and preserves partial successes. Discovery scans and enrichment run off the main loop. The main loop alone applies candidate scheduling changes. Existing provider and risk checks remain in the admission path. |
| Realtime pricing | `src/chains/solana/rpc/subscriptions/`, `src/chains/solana/pools/fetcher_ops.rs`, `src/services/implementations/pool_pricing_service.rs` | Shares a provider WebSocket subscription across watchers of the same account, releases late acknowledgements for removed watches and rejects late or older snapshots. |
| Risk and strategies | `src/tokens/security/`, `src/chains/solana/runtime.rs`, `src/strategies/{engine,readiness}.rs` | Keeps raw token balances precise, rejects inconsistent concentration inputs and fails closed on malformed authorities. Rule trees bound depth and size and propagate unknown values, including through negation. Adaptive sizing excludes invalid P/L evidence and protects cached results from callers. |
| Paper positions and exits | `src/positions/{cost_basis.rs,operations/,paper/}`, `src/trader/{evaluators,paper,safety}/` | Quantity-weighted DCA cost basis. Tier consumption, fees, balance changes and ledger writes commit together. Legacy partial exits retain their completed tier. Protective exits precede DCA. |
| Copy and confirmations | `src/trader/copy/`, `src/wallets/watch/`, `src/transactions/` | The wallet monitor delivers paper signals to the running entrypoint and isolates failed public transaction lookups. The existing observation path requests confirmed public transactions. The app's RPC transport excludes mutating methods and unsigned simulation requires an explicit result. |
| SQLite and dashboard | `src/database/{backup.rs,recovery.rs,health.rs}`, `src/webserver/routes/dashboard/{calendar.rs,overview.rs}` | Verified offline restore to a new file, concurrent dashboard backup retention and actionable corruption responses. The authenticated performance endpoint and dashboard show a 30-day UTC realized paper P/L history. |
| Runtime, logging and CI | `src/run/`, `src/process/`, `src/logger/redact.rs`, `.github/workflows/ci.yml` | SIGINT and SIGTERM stop the main loop and close the dashboard listener. Telegram and PumpPortal connection diagnostics omit potentially secret-bearing exception details. CI runs the offline suite in isolated SQLite storage with bounded duration and least-privilege checkout. |

## Test results

| Status | Check | Result |
| --- | --- | --- |
| Passed | Python 3.12 CI command | 566 tests, including the six discovery failures reproduced on the baseline. |
| Passed | Offline process integration | Real paper process with mock feeds, authenticated readiness, HTTP 401 without credentials, SQLite integrity, unsigned RPC guard and SIGTERM. |
| Passed | Paper smoke with public feeds | Eight healthy discovery feeds, authenticated dashboard health/readiness/performance, verified SQLite integrity and clean SIGTERM. PumpPortal completed a WebSocket handshake and streamed token events. |
| Passed | Backup and recovery | Snapshot, restore into a separate SQLite file and reload with the expected paper balance. Tests cover malformed snapshots and backup requests. |
| Passed | Devnet | Read-only RPC `getHealth` and confirmed `getSlot`. |
| Skipped | Optional provider integrations | Birdeye needed an API key. Raptor production quotes, authenticated Jupiter rates, Telegram delivery and external wallet watch were not validated. |
| Skipped | Transactions | Live signing, on-chain settlement and broadcast are excluded. Unsigned simulation behavior is covered with mocked responses. |
| Pending | Sustained operation | No extended paper run, fill-quality or profitability benchmark, or browser-level parity comparison. |

The cloud proxy accepted the public discovery requests used in the smoke test.
The six baseline discovery failures and a shutdown-time executor race found
during verification were corrected before the passing runs.

## Remaining gaps

- Separate copy-wallet books, reconnect backfill, per-wallet execution state
  and broader quote-settled paper fills remain open.
- Automatic recovery and quarantine, periodic snapshots and upgrade manifests
  remain open. The restore CLI works with a verified snapshot and separate
  destination.
- Revision-scoped paper readiness, full strategy-node traces, broader candle
  feed management, and independently validated mainnet pool coverage remain open.
- ScreenerBot's agent pairing, MCP permissions and approval tools, desktop
  packaging, localization and additional dashboard workspaces are inventoried.
  They do not have equivalent Trojan implementations here.
- Custody, transaction builders that submit swaps, signing, real trade
  confirmation and settlement are excluded by the locked execution scope.

The tested branch is suitable for a longer controlled paper run. The current
evidence establishes functional improvements to Trojan and does not establish
feature or performance parity with ScreenerBot.
