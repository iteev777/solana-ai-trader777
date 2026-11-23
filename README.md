# Trojan V2 — Solana Paper Scalper

A multi-source Solana token scanner with a dip-reversal entry model, multi-timeframe technical analysis, owner-verified on-chain pricing, and rapid paper-position management.

## Current operating mode

- **Paper trading only.** Real-money execution remains locked.
- Scans GeckoTerminal, Birdeye, DexScreener profiles/boosts and PumpPortal feeds concurrently.
- Uses 5-minute price change and buy/sell flow to wait for a small positive rebound instead of buying a falling candle or chasing a vertical pump.
- Rechecks WATCH candidates while their discovery window is active.
- Monitors paper positions frequently, with a 9% stop loss, 10% take-profit target, 4% trailing activation, 3.5% trailing distance, and a 15-minute rotation exit.
- Paper P/L includes an estimated 0.75% fee on each swap side. Actual Solana execution costs and slippage vary.
- Market prices are mapped only to the DexScreener pair's base token; a base-token USD price is never assigned to the quote token.
- GeckoTerminal new-pool results carry normalized market snapshots into analysis; known Gecko pool addresses are retained with paper positions and used as a rate-limited fallback when DexScreener has no complete quote. Discovery-provided pool addresses are also used for a token-mint-checked Gecko fallback during candidate analysis. Gecko OHLCV indicators are applied only when the candidate mint is the pool's base token. This is an API pool-data fallback, not a direct on-chain reserve decoder.
- Token filter decisions (pass, wait, reject, error) are stored in SQLite with reasons and exposed through the dashboard filtering analytics endpoint.

## Important limitations

A true market bottom cannot be known in advance. The entry model looks for a short-window rebound and buyer dominance; it does not guarantee profitable entries. Small targets can be erased by real swap fees, slippage, failed transactions, and latency. Validate with extended paper runs before considering any live execution.

## Run

Install dependencies from `requirements.txt`, configure any optional API keys as environment variables, then run:

```bash
python main.py
```

Keep the bot in paper mode while validating signal quality, net P/L, drawdown, trade count, and API reliability.

### Risk limits

The risk manager reads these optional Railway variables (values below are the defaults):

- `TROJAN_MAX_TRADE_SIZE_USD=5`
- `TROJAN_MIN_TRADE_SIZE_USD=1`
- `TROJAN_MAX_DAILY_LOSS_USD=50` (default $50 paper daily loss cap; an explicit Railway variable overrides it)
- `TROJAN_MAX_TRADE_BALANCE_FRACTION=0.10` (each new position is capped at 10% of remaining balance)
- `TROJAN_MAX_MAIN_POSITIONS=10`
- `TROJAN_MAX_SNIPER_POSITIONS=10`
- `TROJAN_MAX_TOTAL_POSITIONS=15`
- `TROJAN_STOP_LOSS_PERCENT=20`
- `TROJAN_TAKE_PROFIT_PERCENT=30`

The strategy engine automatically scales the paper order size down as the account balance falls. A trade is blocked if its size would exceed the configured balance fraction or the minimum ticket cannot be met.


## Read-only dashboard

The bot can serve an authenticated dashboard on Railway. Set the Railway variable `TROJAN_DASHBOARD_TOKEN` to a long random secret before starting the service. The dashboard uses HTTP Basic Authentication (username can be any value; password is the token), listens on Railway's `PORT`, and exposes read-only portfolio, paper-trading, queue, API, market-data and event information. No wallet keys or live-trading controls are exposed. If the token is not configured, the dashboard stays disabled.


## Configurable strategy rules

The optional strategy rule engine supports nested `all`, `any`, and `not` groups with numeric comparisons. Configure it in Railway with the `TROJAN_STRATEGY_RULES` environment variable (JSON). If the variable is empty, the existing strategy behavior is unchanged. Example:

```json
{
  "main": {
    "all": [
      {"field": "liquidity_usd", "op": "gte", "value": 12000},
      {"field": "buy_ratio", "op": "gte", "value": 0.58},
      {"field": "price_change_m5", "op": "between", "value": [0.1, 4.0]}
    ]
  },
  "sniper": {
    "all": [
      {"field": "liquidity_usd", "op": "gte", "value": 3000},
      {"field": "buy_ratio", "op": "gte", "value": 0.55}
    ]
  }
}
```

Supported fields: `liquidity_usd`, `volume_m5`, `volume_h24`, `price_change_m5`, `price_change_h1`, `price_change_h24`, `buy_ratio`, `score`, `source_count`, `txns_m5_total`, `pair_age_minutes`, `rsi_14`, `ema_9`, `ema_21`, `ema_cross_bullish`, `breakout_20`, `volume_spike_ratio`, `timeframe_ready_count`, `bullish_timeframes`, `bearish_timeframes`, `macd_histogram`, `macd_bullish`, `bollinger_percent_b`, `atr_percent`, `stochastic_k`, and `above_vwap`. Supported operators: `gt`, `gte`, `lt`, `lte`, `eq`, and `between`. The dashboard reports whether the custom rules are enabled and valid at `/api/strategies`. Live execution remains locked.


## Paper position management

- A partial take-profit closes 50% of a position once it reaches +8% ROI; the remaining half continues under the normal stop, take-profit, trailing, and time-exit rules. Set `TROJAN_PARTIAL_TAKE_PROFIT_ENABLED=false` to disable it.
- One DCA add of $2.50 is available after a 5% pullback, but it is **disabled by default**. Set `TROJAN_DCA_ENABLED=true` to enable it in paper mode. The add is blocked once the existing hard stop threshold has been reached and is limited to one add per position.
- DCA and partial exits are recorded as separate paper transactions; realized P/L includes the configured estimated entry and exit fees.


## On-chain pool verification and technical indicators

Configure `SOLANA_RPC_URL` with a Solana mainnet RPC endpoint to enable read-only pool account decoding. Optional `SOLANA_WS_URL` is the matching websocket endpoint. The decoder verifies the program owner and account discriminator/layout before reading pool state. Supported families: PumpSwap, Pump.fun legacy bonding curves, Raydium AMM v4/CPMM/CLMM, Orca Whirlpool, Meteora DLMM/DAMM v2/DBC, FluxBeam AMM, and Moonit. Active paper-position pools are watched through a shared `accountSubscribe` WebSocket connection; notifications trigger fresh verified snapshots and stream latency is exposed in the dashboard. Unsupported layouts fall back to market-data providers; the bot does not label those as on-chain quotes.

OHLCV candles are fetched from GeckoTerminal per known pool and stored in SQLite. The indicator engine calculates RSI(14), EMA(9/21), MACD, Bollinger Bands, ATR(14), Stochastic, VWAP, OBV, 20-candle breakout and volume-spike ratio. Seven views (1m, 3m, 5m, 15m, 30m, 1h and 4h) are aggregated from the 1m source; higher timeframes stay not-ready until enough candles exist. The optional technical entry filter is enabled by default when sufficient candles are available; it does not fabricate indicators when history is missing. Set `TROJAN_TECHNICAL_FILTER_ENABLED=false` to disable the extra confirmation. `TROJAN_OHLCV_MIN_INTERVAL` controls the minimum API request spacing (default 7.2 seconds).

## Dashboard strategy editor

The authenticated dashboard now has MAIN and SNIPER JSON rule editors. Save operations write to SQLite and override environment-based rules for that strategy. Use an empty JSON object (`{}`) to clear a saved override and return to the default strategy. Indicator fields include RSI/EMA, MACD, Bollinger position, ATR, Stochastic, VWAP, OBV, timeframe readiness and trend consensus.

## Telegram and copy-wallet monitoring

Optional environment variables:

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: send paper BUY/SELL, partial-exit and copy-watch alerts.
- `TROJAN_COPY_WALLETS`: comma-separated public Solana wallet addresses (maximum 20) to monitor.
- `SOLANA_WS_URL`: websocket endpoint for `logsSubscribe`; if omitted, it is derived from `SOLANA_RPC_URL` when possible.

Copy-wallet tracking reads public wallet activity, stores unique signatures in SQLite and sends alerts. Set `TROJAN_COPY_PAPER_ENABLED=true` to mirror BUY/SELL signals in the paper ledger only. Paper copy limits default to 3 open positions, $25 total exposure and $5 per ticket per followed wallet; override with `TROJAN_COPY_MAX_POSITIONS_PER_WALLET`, `TROJAN_COPY_MAX_EXPOSURE_PER_WALLET_USD` and `TROJAN_COPY_MAX_TRADE_SIZE_USD`. Because the paper ledger is keyed by mint, a token already held by another wallet or strategy is rejected rather than mixing ownership. No private key is used and no live transaction is signed or broadcast. Never enter a wallet seed phrase or private key into these settings.

## Jupiter route intelligence

Trojan uses Jupiter Swap API v2 `/order` in quote-only mode (no taker address and no transaction request). Each quote records provider/router, route venues and hop count, split-route details, platform fee basis points, price impact, minimum output, quote latency and context slot. The dashboard/API integration can use this data for route-quality review; quote preparation does not authorize execution. Configure `JUPITER_API_KEY` for authenticated production rate limits and optionally override `TROJAN_JUPITER_ORDER_URL`. Signing, transaction simulation and broadcast remain disabled in code.

## Health and readiness

The dashboard exposes `/api/readiness`, `/api/copy-trades`, `/api/filtering`, and `/api/performance`. The strategy-performance view counts closed SELL trades only and reports realized P/L and win rate separately for MAIN and SNIPER. Paper readiness checks database integrity, the main-loop and position-monitor heartbeats, and the market API client. RPC, Telegram and wallet monitoring are reported as optional integrations. Live readiness is always false and live execution remains locked.

## RPC failover and adaptive paper learning

- Configure one or more Solana RPC HTTP endpoints with `SOLANA_RPC_URL` or comma-separated `SOLANA_RPC_URLS`.
- Optional failover endpoints: `HELIUS_RPC_URL`, `QUICKNODE_RPC_URL`, `TRITON_RPC_URL`, and `ALCHEMY_RPC_URL`.
- RPC 429 responses trigger per-endpoint cooldown and failover; dashboard health reports endpoint hosts and counters without exposing URLs or credentials.
- The account WebSocket coalesces repeated pool notifications to avoid redundant RPC snapshots. The default refresh floor is 500 ms per watched pool; this is a throttle, not a guaranteed end-to-end price latency.
- Adaptive learning reads paper-mode realized sell outcomes only. After the configured minimum sample (default 30), it adjusts future paper position size between 0.80x and 1.15x. It does not alter live execution, and live execution remains locked.
- Additional strategy fields include candle body/range size, consecutive green/red candles, volume-spike state and holding-time input.

## Automated safety tests

Run `python -m unittest discover -s tests -v` to check the risk caps, malformed market-data rejection, and the DexScreener base-token price invariant. GitHub Actions runs the same suite on feature-branch pushes and pull requests.


## Pre-entry routing, unsigned simulation and token security

Every strategy-approved paper entry now receives a Jupiter Swap API v2 quote for the intended USD ticket size, converted to SOL lamports using the live SOL/USD feed. Trojan evaluates price impact, platform fee, route venues/hops, split routing and quote latency before allowing the paper entry. Route quality is scored and checked against `TROJAN_MIN_ROUTE_QUALITY_SCORE` (default 55); price impact is capped by `TROJAN_MAX_PRICE_IMPACT_PERCENT` (default 3%). Slippage is capped by `TROJAN_MAX_SLIPPAGE_BPS` (default 100). When both `TROJAN_PUBLIC_WALLET_ADDRESS` and `SOLANA_RPC_URL` are configured, Trojan requests a Jupiter order for that public address and simulates the unsigned transaction with Solana `simulateTransaction`, `sigVerify=false` and `replaceRecentBlockhash=true`. The transaction is never signed, submitted, or broadcast. Set `TROJAN_REQUIRE_UNSIGNED_SIMULATION=true` to reject entries when this simulation cannot run. Jupiter keyless access is rate-limited; configure `JUPITER_API_KEY` for production use.

When a mainnet RPC is configured, a separate mint-security gate verifies the actual SPL/Token-2022 mint account, requires mint and freeze authorities to be revoked, and inspects the largest token accounts. The concentration metric is explicitly token-account-authority concentration, not beneficial-owner concentration; pool vaults and custody accounts can be included. RugCheck's holder-risk report remains an independent gate. Missing or malformed on-chain authority data fails closed.

## Copy-wallet performance and daily risk

Paper trade rows now preserve the originating copy wallet through entry, DCA, partial exits and final exits. The dashboard reports wallet-attributed realized P/L, win rate, open positions/exposure and observed wallet activity. Copy trading also has a separate per-wallet daily realized-loss circuit breaker: `TROJAN_COPY_MAX_DAILY_LOSS_PER_WALLET_USD=10` by default, alongside the existing per-wallet position, exposure and ticket limits. The daily loss is checked before queuing and again immediately before paper entry.

## Eleven-protocol mainnet validation telemetry

Each observed pool that reaches the on-chain decoder is recorded by the protocol family actually identified from the pool account owner and verified snapshot. The dashboard's protocol-validation panel reports real confirmed-RPC decode attempts, successes, failures and last errors for all eleven supported families. A zero count means no real pool sample for that protocol has yet been validated by this deployment; decoder unit tests do not count as mainnet validation. This telemetry is intentionally evidence-based and does not mark an unobserved protocol as verified.


### Native pool quote foundation (read-only)

`protocol_quotes.py` contains integer exact-input quote math for Raydium
CPMM when supplied with a fresh, decoded pool snapshot. The snapshot must come
from one consistent on-chain read and include reserves net of accrued protocol,
fund and creator fee balances, the active fee configuration, pool status, slot
and observation timestamp. Token-2022 transfer-fee mints and unsupported creator
fee modes fail closed. The module does not yet decode Raydium account layouts or
fetch pool snapshots; therefore it is not advertised as a live direct-pool
provider and is not included in best-route selection. Jupiter/Raptor remain the
only live quote providers until the decoder and integration are independently
validated. No transaction signing or broadcasting is added.


### Raydium CPMM direct quote (read-only)

The route comparison endpoint accepts an optional `pool_address` query parameter.
When supplied, Trojan fetches the Raydium CPMM pool, then fetches the pool state,
AmmConfig, both vaults and both mint accounts together through one confirmed
`getMultipleAccounts` context slot. It subtracts accrued protocol, fund and
creator fee balances from vault reserves, reads the current fee tier, and
accounts for creator fees charged on either the input or output side. Token-2022
and unsupported token programs fail closed until transfer-fee-aware quoting is
implemented. The result is compared by minimum output alongside Jupiter and
Raptor. This remains quote-only: no transaction is built, signed or broadcast.

Example:
`/api/route-comparison?input_mint=...&output_mint=...&amount=1000000&pool_address=...`



## Pool account monitoring

The read-only Solana pool monitor tracks active discovery candidates and open positions. It subscribes to pool state and reserve vault accounts, then refreshes complete per-pool snapshots in shared confirmed RPC batches (50 accounts per batch). Candidate watches expire automatically and the batch heartbeat recovers missed WebSocket notifications.

Optional Railway environment variables:

- `TROJAN_POOL_BATCH_POLL_INTERVAL`: batch refresh heartbeat in seconds (default `0.5`, minimum `0.5`).
- `TROJAN_CANDIDATE_WATCH_TTL`: candidate watch lifetime in seconds (default `300`, minimum `30`).
- `TROJAN_MAX_CANDIDATE_WATCHES`: maximum discovery-candidate watches (default `100`, minimum `10`).
- `TROJAN_POOL_REFRESH_INTERVAL`: WebSocket notification coalescing interval in seconds (default `0.5`, minimum `0.25`).

Dashboard on-chain status reports position/candidate watches, successful/failed pool refreshes, RPC batch and account counts, and the latest refresh latency. This subsystem is read-only; it does not sign or broadcast transactions.


## Multi-source pool registry

The fast market-data path uses the DexScreener batch endpoint. In the background, each token is refreshed through DexScreener's full token-pairs endpoint (up to 30 pools). If that list is empty, capped, or has no SOL-native pair, the registry completes it through GeckoTerminal's token-pools endpoint. Pools are merged by pool address, source provenance is retained, and the canonical selector prefers SOL-native reserve liquidity with an 80% incumbent keep threshold.

GeckoTerminal requests share the API client's host-wide rate limiter and 60-second response cache with the existing Gecko discovery scanners. Registry jobs run in a bounded background queue and never block the trading loop. The dashboard reports separate DexScreener and GeckoTerminal queues, completed/failed jobs, and registered token count.


## Pool registry persistence

Pool candidates and their provider provenance are stored in SQLite with a bounded write cadence and restored on process startup. Old snapshots expire after five minutes and fresh provider data replaces them. This uses the same database file as paper/live ledger records; durable storage across Railway deployments requires the service to mount a persistent Railway volume at the configured BOT_DATA_DIR.


## Pool analysis resilience

Pool RPC transport failures (timeouts, provider outages and rate limits) are kept separate from structural pool/layout failures. Transient failures use per-pool exponential backoff from 60 seconds up to 10 minutes; structural failures are quarantined for one hour. The pool refresher runs up to four independent confirmed account batches concurrently and continues refreshing healthy pools when one batch fails. Only snapshots explicitly marked on-chain verified are published. Structurally invalid pools are rejected rather than used with external-only prices. Dashboard health exposes active transient backoffs, structural quarantines and skipped pool refreshes.

## Multi-stage token discovery and candidate ranking

Trojan V2 combines independent Solana discovery feeds (DexScreener profiles/boosts/community takeovers, GeckoTerminal new and trending pools, Birdeye listings/trending, PumpPortal new-token stream, RugCheck feeds, Jupiter token lists, CoinGecko Solana metadata and DeFiLlama protocol metadata). Results are deduplicated by mint while preserving source provenance and pool metadata.

Before scheduling analysis, the discovery loop fills missing market-data snapshots in bounded batches and ranks candidates using liquidity, 5m/1h/6h/24h volume, buy/sell flow, source agreement, pool age, and multi-window momentum. Candidate tiers (strong/watch/weak/reject) are exposed through `/api/discovery` and the dashboard. Incomplete data remains visible as unknown and is not silently converted into a rejection. Only explicit invalid market facts (for example zero liquidity or a non-positive quoted price) are hard-rejected by this ranking stage. Scores are prioritisation signals, not buy signals, and do not unlock live trading.


## Ordered candidate filtering

Before strategy evaluation, Trojan runs an auditable filter pipeline in this order: metadata and symbol integrity, market price/liquidity integrity, pool age, valuation, transaction/volume activity, short-window momentum, discovery-source provenance, and optional candidate-quality score. Each stage returns a distinct `PASS`, `WAIT`, or `REJECT` result with a reason and measured inputs. Missing optional data is not fabricated as zero.

Optional Railway variables (unset defaults preserve the current strategy behavior):

- `TROJAN_FILTER_MIN_LIQUIDITY_USD` (default: $2,500 for sniper; $0 additional floor for main)
- `TROJAN_FILTER_MIN_POOL_AGE_MINUTES` (default: 0)
- `TROJAN_FILTER_MAX_POOL_AGE_MINUTES` (default: no practical upper bound)
- `TROJAN_FILTER_REQUIRE_POOL_AGE` (default: false)
- `TROJAN_FILTER_MAX_FDV_USD` (default: no practical cap)
- `TROJAN_FILTER_MIN_LIQUIDITY_FDV_RATIO` (default: 0, disabled)
- `TROJAN_FILTER_MIN_VOLUME_5M_USD` (default: 0, disabled)
- `TROJAN_FILTER_MIN_TXNS_5M` (default: 0, disabled)
- `TROJAN_FILTER_MIN_BUY_RATIO` (default: 0, disabled; range 0–1)
- `TROJAN_FILTER_MIN_CHANGE_5M_PCT` (default: -100)
- `TROJAN_FILTER_MAX_CHANGE_5M_PCT` (default: 10,000)
- `TROJAN_FILTER_REQUIRE_M5_CHANGE` (default: false)
- `TROJAN_FILTER_MIN_DISCOVERY_SOURCES` (default: 0, disabled)
- `TROJAN_FILTER_MIN_QUALITY_SCORE` (default: 0, disabled; range 0–100)

Thresholds should be calibrated from paper-trading evidence before being tightened. A filter pass is not a buy signal; strategy, RugCheck, on-chain security, route quality and risk checks remain separate gates.


## Named strategy conditions

The strategy rule tree supports both numeric field leaves and named conditions. The read-only `/api/strategy-catalog` endpoint exposes 24 named condition types: RSI, Bollinger Bands, buy pressure, candle size/streaks, drawdown/pullback, liquidity level/change/liquidity-to-size, moving-average cross and price-to-MA, position adds/holding time/profit/giveback, price breakout/change, UTC time window, token age, transaction velocity, ATR volatility, volume spike, and VWAP.

Example named leaf:

```json
{
  "main": {
    "all": [
      {"condition": "Rsi", "params": {"comparison": "BELOW", "threshold": 65}},
      {"condition": "BuyPressure", "params": {"threshold": 0.58}},
      {"condition": "VolumeSpike", "params": {"multiplier": 1.5}}
    ]
  }
}
```

Named conditions can be nested in the existing `all`, `any`, and `not` groups. Parameter ranges and enum choices are validated before saving. If a condition requires unavailable candle or position data, it fails closed with an explicit reason rather than inventing a value. Position-only conditions require the corresponding position context to be supplied; they do not become entry signals by themselves. Validate strategy changes in paper mode before tightening live-facing thresholds.


## Native direct quote adapters

The direct-pool quote comparison dispatches from the pool account's verified program owner. Confirmed-snapshot exact-input math is currently implemented for Raydium CPMM and Raydium AMM v4. Raydium AMM v4 quotes read the on-chain fee numerator/denominator and subtract accrued `need_take_pnl` from vault balances before applying constant-product math. Token-2022 transfer-fee mints are refused until a dedicated adapter is implemented. Other decoded DEX protocols remain explicitly unavailable for native direct quotes rather than receiving guessed fee/curve math. Jupiter and Raptor comparisons remain independent quote-only routes. Signing and broadcast remain disabled.


## Configurable paper exit policy

The paper engine evaluates exit rules in a deterministic order: hard stop-loss, peak-armed trailing protection, optional break-even protection, take-profit, optional stagnation exit, then maximum holding time. The trailing stop is armed from the persisted peak price (not the current mark), so a pullback cannot disarm it. Missing/invalid prices never manufacture an exit.

Optional environment settings (defaults preserve the previous 9% stop, 10% take-profit, 4% trailing activation, 3.5% trailing distance and 900-second max hold):

- `TROJAN_STOP_LOSS_PCT`
- `TROJAN_TAKE_PROFIT_PCT`
- `TROJAN_TRAILING_ACTIVATION_PCT`
- `TROJAN_TRAILING_DISTANCE_PCT`
- `TROJAN_MAX_HOLD_SECONDS` (0 disables time exit)
- `TROJAN_STAGNATION_SECONDS` (0 disables stagnation exit)
- `TROJAN_STAGNATION_BAND_PCT`
- `TROJAN_BREAK_EVEN_ACTIVATION_PCT` (0 disables break-even protection)
- `TROJAN_BREAK_EVEN_OFFSET_PCT`

These settings affect the paper engine only. They do not authorize live trading or alter the live execution lock.


## Copy-wallet event classification

Copy-watch now derives token changes from the watched wallet's raw SPL/Token-2022 balance deltas (aggregated by mint before UI conversion), removes the transaction fee from the wallet's native-SOL economic delta, and records the invoked swap programs. A BUY/SELL signal is emitted only when the watched wallet is the transaction fee payer, a recognised swap program was invoked, and token movement is paired with an opposing quote-asset movement. Ordinary transfers, sponsored transactions and ambiguous balance flows remain `TOKEN TRANSFER / UNKNOWN` and are never queued as paper-copy trades. Additional supported router/program IDs can be supplied with `TROJAN_COPY_SWAP_PROGRAM_IDS` (comma-separated). This is conservative classification, not a guarantee that every DEX route can be recognized.


FluxBeam is also available as a read-only direct quote adapter for the verified `SwapV1` ConstantProduct curve. It reads both fee ratios from the pool account, rounds each input fee upward, applies the exact constant-product reserve step, and uses vault/mint accounts fetched in one confirmed RPC context. Other curve types and Token-2022 transfer-fee mints are refused. Direct route comparison now checks that the pool quote's output mint exactly matches the requested output mint before comparing raw minimum outputs.


## SQLite backup and persistence health

The dashboard's System page exposes SQLite integrity, WAL mode, storage backend, database/WAL size and verified backup count. **Create verified backup** uses SQLite's online backup API (safe with WAL and active readers/writers), runs `PRAGMA integrity_check` on the snapshot, and retains the newest seven snapshots by default. The authenticated endpoint is `POST /api/database/backup`; `TROJAN_BACKUP_DIR` can select a separate backup directory.

Railway durability still requires an attached persistent volume. The health panel reports `ephemeral_default` when neither `RAILWAY_VOLUME_MOUNT_PATH` nor `BOT_DATA_DIR` is configured. A backup stored on the same ephemeral filesystem is not disaster recovery; attach a Railway volume or configure an external backup destination before relying on long-term retention.

## Multi-window market filtering

The deterministic candidate filter now evaluates volume, transaction activity and price momentum independently across 5m, 1h, 6h and 24h windows. Optional volume floors use `TROJAN_FILTER_MIN_VOLUME_5M_USD`, `TROJAN_FILTER_MIN_VOLUME_1H_USD`, `TROJAN_FILTER_MIN_VOLUME_6H_USD` and `TROJAN_FILTER_MIN_VOLUME_24H_USD`; transaction floors use `TROJAN_FILTER_MIN_TXNS_5M`, `TROJAN_FILTER_MIN_TXNS_1H`, `TROJAN_FILTER_MIN_TXNS_6H` and `TROJAN_FILTER_MIN_TXNS_24H`. Buy-pressure floors use `TROJAN_FILTER_MIN_BUY_RATIO` for 5m (backward compatible) and `TROJAN_FILTER_MIN_BUY_RATIO_1H`, `_6H`, `_24H` for longer windows. Momentum bands use `TROJAN_FILTER_MIN_CHANGE_{5M,1H,6H,24H}_PCT` and `TROJAN_FILTER_MAX_CHANGE_{5M,1H,6H,24H}_PCT`; missing-window requirements can be enabled with `TROJAN_FILTER_REQUIRE_{5M,1H,6H,24H}_CHANGE=true`.

Each window is checked independently, one-sided transaction counts are retained as reported, and absent measurements are only held when the corresponding floor/requirement is enabled. The filter report includes per-window raw measurements and derived buy ratios. Existing 5m environment variables and rejection codes remain compatible.




## Expanded RugCheck risk intelligence

RugCheck analysis now retains a structured signal report and evaluates more than the normalized score and individual risk list. It includes the largest holder and sorted top-ten concentration, creator share (from creator holder attribution or creator balance / supply), RugCheck's rugged flag, token mutability combined with freeze authority, metadata update authority, transfer-fee percentage and authority, graph-linked insider count, LP-provider count and reported market liquidity. These remain separate from on-chain token-account authority concentration, which is not a beneficial-owner identity check.

Configurable risk thresholds:
- `TROJAN_RUGCHECK_MAX_TOP_HOLDER_PCT` (50), `TROJAN_RUGCHECK_WARN_TOP_HOLDER_PCT` (30)
- `TROJAN_RUGCHECK_MAX_TOP10_PCT` (80), `TROJAN_RUGCHECK_WARN_TOP10_PCT` (60)
- `TROJAN_RUGCHECK_MAX_CREATOR_PCT` (20), `TROJAN_RUGCHECK_WARN_CREATOR_PCT` (10)
- `TROJAN_RUGCHECK_MAX_TRANSFER_FEE_PCT` (10)
- `TROJAN_RUGCHECK_MAX_GRAPH_INSIDERS` (3)
- `TROJAN_RUGCHECK_MAX_MEDIUM_RISKS` (3)

Missing essential RugCheck inputs still produce UNKNOWN and cannot pass the existing buy gate. Missing optional extended fields are exposed as unknown rather than fabricated. A positive transfer fee or active transfer-fee authority adds risk; a rugged token, critical concentration/creator share, excessive transfer fee, or other high-severity signal rejects the report.


## Dynamic copy-wallet watchlist

The dashboard System page can add and remove public Solana wallet addresses without editing Railway variables. The list is persisted in SQLite schema v7 and merged with the legacy `TROJAN_COPY_WALLETS` environment list, with a hard cap of 20 active addresses. Dashboard-managed addresses are base58/32-byte validated; environment-managed addresses are displayed as read-only and must be changed in environment configuration. The WebSocket monitor compares the effective list while running and reconnects its subscriptions when the list changes. Copy monitoring remains read-only; paper-copy signals still pass wallet-specific risk limits and the conservative swap-event classifier.

## Extended metadata and market bounds

The filter pipeline supports optional metadata completeness gates: `TROJAN_FILTER_REQUIRE_NAME_AND_SYMBOL`, `TROJAN_FILTER_REQUIRE_LOGO_URL` and `TROJAN_FILTER_REQUIRE_WEBSITE_URL`. Logo and website requirements accept only HTTP(S) URLs from supported provider metadata. Suspicious single-character symbols can be controlled with `TROJAN_FILTER_REJECT_SINGLE_CHAR_SYMBOL` (enabled by default); numeric-only symbols remain rejected.

Liquidity can use both `TROJAN_FILTER_MIN_LIQUIDITY_USD` and `TROJAN_FILTER_MAX_LIQUIDITY_USD` (maximum 0 disables the ceiling). Valuation bands are independently configurable with `TROJAN_FILTER_MIN_FDV_USD`, `TROJAN_FILTER_MAX_FDV_USD`, `TROJAN_FILTER_MIN_MARKET_CAP_USD` and `TROJAN_FILTER_MAX_MARKET_CAP_USD`. A positive floor with missing data returns WAIT; a configured ceiling only applies when the measurement exists. Invalid negative valuations are rejected. Existing default behavior remains permissive when optional metadata and floors are disabled.



RugCheck's structured `analysis` and `signals` are carried with each candidate's approval or rejection result. The same report object is evaluated and propagated, avoiding a second provider request and ensuring the dashboard/API can inspect the exact risk evidence behind the gate. Missing or invalid reports remain rejected; the propagation does not weaken the gate.

## PumpSwap direct quote adapter

The read-only direct quote router now recognises PumpSwap pools from the on-chain program owner. It reads the pool, GlobalConfig, Pump Fees FeeConfig, both vaults and both mint accounts at one confirmed RPC slot; validates the account owners, discriminators, pool/vault/mint relationships, trade-disable flags, effective quote reserve (vault amount plus signed virtual quote reserve), and fee configuration. It calculates both quote-in buys and base-in sells, includes the separate LP/protocol/creator fee components, and returns slippage-adjusted minimum output and snapshot telemetry.

The current adapter deliberately uses the highest total fee found across the on-chain flat, standard-tier, stable-tier and exotic fee entries. This conservative policy avoids assuming an unverified pool classification or a cheaper market-cap tier, but can understate the output of pools whose actual fee tier is lower. Token-2022, cashback pools and holder-reward pools remain refused until their extra token/fee behavior is independently modelled. This is a quote-only adapter: signing, transaction construction and broadcast remain disabled.



## RugCheck request resilience

RugCheck report retrieval coalesces simultaneous requests for the same mint into one upstream request, uses a bounded 2,000-entry least-recently-used cache, and measures cache age with a monotonic clock. Cache lifetime is configurable with `TROJAN_RUGCHECK_CACHE_SECONDS` (default 60 seconds, accepted range 0–3,600; set 0 to disable caching). Failed, rate-limited and invalid responses are never cached, and stale PASS reports are not used as a fallback when the provider is unavailable. This reduces duplicate provider load without weakening the fail-closed security decision.


## Paper position input safety

Paper entry and exit paths reject non-finite or non-positive prices before reaching the database. Persisted trailing peaks are normalized before use, and DCA/exit policy configuration is fully parsed before a monitoring cycle mutates position state. Invalid policy configuration returns a controlled wait/error result rather than interrupting the position monitor. Reserved engine, strategy and adaptive-sizing metadata cannot be overwritten by caller metadata.


## Advanced paper exit protection

Trailing protection can use peak-profit tiers through `TROJAN_TRAILING_BANDS_JSON`, for example `[{"activation_pct":10,"distance_pct":5},{"activation_pct":20,"distance_pct":3}]`. Thresholds must increase and trailing distances may only stay equal or tighten as peak profit rises. The selected tier is based on the persisted peak since entry; trailing-profit exits only fire while the current mark remains above entry. An optional time-loss rule uses `TROJAN_TIME_LOSS_EXIT_SECONDS` (0 disables it) and `TROJAN_TIME_LOSS_THRESHOLD_PCT` (default -10%). Existing fixed stop, take-profit, break-even, stagnation and max-hold rules remain available.

## Technical indicator calculation conventions

OHLCV analysis now uses SMA-seeded exponential moving averages, Wilder-smoothed RSI(14) and ATR(14), and a conventionally aligned MACD(12,26,9). Malformed candles are excluded, MACD stays explicitly unavailable until its full signal history exists, and the indicator payload reports the calculation methods used. This avoids treating short or incomplete history as a fabricated technical signal.



## OHLCV history and cache

GeckoTerminal OHLCV retrieval requests up to the provider's documented 1,000-candle maximum, improving the available 1m-derived 5m/15m/30m indicator history. The indicator cache is bounded to 100 pools with a 60-second monotonic TTL and LRU eviction. The configurable request interval is clamped to at least six seconds to stay within the public API's approximately 10 calls/minute budget; cache status exposes the current limits. Higher timeframes remain explicitly not-ready when the available history is insufficient.

Moonit direct quotes are read-only and limited to the verified ConstantProductV1 native-SOL curve. The adapter validates the ConfigAccount discriminator and fee, uses conservative ceiling arithmetic for remaining virtual reserves, and caps buy output by the curve's actual remaining token amount. Unsupported curve/collateral modes and Token-2022 mints remain refused.




## RugCheck holder and liquidity-lock controls

The RugCheck analyzer supports an optional unique-holder floor with `TROJAN_RUGCHECK_MIN_HOLDERS`. When enabled, missing, zero, negative or fractional holder counts produce an UNKNOWN security result rather than passing. A reported count below the floor is rejected.

LP-lock requirements can be configured separately: `TROJAN_RUGCHECK_MIN_PUMPFUN_LP_LOCK_PCT` for Pump-family tokens and `TROJAN_RUGCHECK_MIN_REGULAR_LP_LOCK_PCT` for other token types. The older `TROJAN_RUGCHECK_MIN_LP_LOCK_PCT` remains the fallback for Pump-family tokens. If a configured LP-lock floor cannot be verified from the RugCheck report, the security result is UNKNOWN. The report exposes the observed lock, required floor and holder count in its structured signals.



## Pump.fun legacy bonding-curve direct quote

The read-only direct quote path supports legacy SOL-paired Pump.fun curves using the on-chain Global and Pump Fees configuration. It verifies the bonding-curve PDA against the requested token mint, reads curve/global/fee/mint accounts at one confirmed slot, selects the market-cap fee tier, and validates both virtual pricing reserves and real settlement reserves. Mayhem, cashback, holder-reward, custom creator-fee, custom quote-mint, completed, truncated and Token-2022 curves are refused until their exact fee/settlement behavior is implemented. The route comparison forwards the requested output mint to this adapter; no instruction is constructed or signed.
