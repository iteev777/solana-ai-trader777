# Trojan V2 — Solana Paper Scalper

A multi-source Solana token scanner with a dip-reversal entry model and rapid paper-position management.

## Current operating mode

- **Paper trading only.** Real-money execution remains locked.
- Scans DexScreener discovery feeds concurrently and listens for new PumpPortal tokens.
- Uses 5-minute price change and buy/sell flow to wait for a small positive rebound instead of buying a falling candle or chasing a vertical pump.
- Rechecks WATCH candidates while their discovery window is active.
- Monitors paper positions frequently, with a 9% stop loss, 10% take-profit target, 4% trailing activation, 3.5% trailing distance, and a 15-minute rotation exit.
- Paper P/L includes an estimated 0.75% fee on each swap side. Actual Solana execution costs and slippage vary.
- Market prices are mapped only to the DexScreener pair's base token; a base-token USD price is never assigned to the quote token.
- GeckoTerminal new-pool results carry normalized market snapshots into analysis; known Gecko pool addresses are retained with paper positions and used as a rate-limited fallback when DexScreener has no quote. This is an API pool-data fallback, not a direct on-chain reserve decoder.
- Token filter decisions (pass, wait, reject, error) are stored in SQLite with reasons and exposed through the dashboard filtering analytics endpoint.

## Important limitations

A true market bottom cannot be known in advance. The entry model looks for a short-window rebound and buyer dominance; it does not guarantee profitable entries. Small targets can be erased by real swap fees, slippage, failed transactions, and latency. Validate with extended paper runs before considering any live execution.

## Run

Install dependencies from `requirements.txt`, configure any optional API keys as environment variables, then run:

```bash
python main.py
```

Keep the bot in paper mode while validating signal quality, net P/L, drawdown, trade count, and API reliability.


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

Supported fields: `liquidity_usd`, `volume_m5`, `volume_h24`, `price_change_m5`, `price_change_h1`, `price_change_h24`, `buy_ratio`, `score`, `source_count`, `txns_m5_total`, and `pair_age_minutes`. Supported operators: `gt`, `gte`, `lt`, `lte`, `eq`, and `between`. The dashboard reports whether the custom rules are enabled and valid at `/api/strategies`. Live execution remains locked.


## Paper position management

- A partial take-profit closes 50% of a position once it reaches +8% ROI; the remaining half continues under the normal stop, take-profit, trailing, and time-exit rules. Set `TROJAN_PARTIAL_TAKE_PROFIT_ENABLED=false` to disable it.
- One DCA add of $2.50 is available after a 5% pullback, but it is **disabled by default**. Set `TROJAN_DCA_ENABLED=true` to enable it in paper mode. The add is blocked once the existing hard stop threshold has been reached and is limited to one add per position.
- DCA and partial exits are recorded as separate paper transactions; realized P/L includes the configured estimated entry and exit fees.


## On-chain pool verification and technical indicators

Configure `SOLANA_RPC_URL` with a Solana mainnet RPC endpoint to enable read-only pool account decoding. Optional `SOLANA_WS_URL` is the matching websocket endpoint. The decoder verifies the pool account owner and binary discriminator before reading vaults. Supported layouts are PumpSwap Pool, Raydium CPMM, Raydium CLMM, Orca Whirlpool, and Meteora DLMM. Raydium AMM v4 and unsupported layouts fall back to the market-data providers; the bot does not pretend those are on-chain quotes.

OHLCV candles are fetched from GeckoTerminal per known pool and stored in SQLite. The indicator engine calculates RSI(14), EMA(9/21), 20-candle breakout and volume-spike ratio. The optional technical entry filter is enabled by default when sufficient candles are available; it does not fabricate indicators when history is missing. Set `TROJAN_TECHNICAL_FILTER_ENABLED=false` to disable the extra confirmation. `TROJAN_OHLCV_MIN_INTERVAL` controls the minimum API request spacing (default 7.2 seconds).

## Dashboard strategy editor

The authenticated dashboard now has MAIN and SNIPER JSON rule editors. Save operations write to SQLite and override environment-based rules for that strategy. Use an empty JSON object (`{}`) to clear a saved override and return to the default strategy. Indicator fields include `rsi_14`, `ema_9`, `ema_21`, `ema_cross_bullish` (1/0), `breakout_20` (1/0), and `volume_spike_ratio`.

## Telegram and copy-wallet monitoring

Optional environment variables:

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: send paper BUY/SELL, partial-exit and copy-watch alerts.
- `TROJAN_COPY_WALLETS`: comma-separated public Solana wallet addresses (maximum 20) to monitor.
- `SOLANA_WS_URL`: websocket endpoint for `logsSubscribe`; if omitted, it is derived from `SOLANA_RPC_URL` when possible.

Copy-wallet tracking is read-only: it identifies token balance changes and SOL movement, stores unique signatures in SQLite and sends alerts. It does not place copy trades or sign transactions. Never enter a wallet seed phrase or private key into these settings.

## Health and readiness

The dashboard exposes `/api/readiness`, `/api/copy-trades`, and the strategy configuration endpoints. Paper readiness checks database integrity, the main-loop and position-monitor heartbeats, and the market API client. RPC, Telegram and wallet monitoring are reported as optional integrations. Live readiness is always false and live execution remains locked.
