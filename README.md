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
