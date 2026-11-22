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
