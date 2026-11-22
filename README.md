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

## Important limitations

A true market bottom cannot be known in advance. The entry model looks for a short-window rebound and buyer dominance; it does not guarantee profitable entries. Small targets can be erased by real swap fees, slippage, failed transactions, and latency. Validate with extended paper runs before considering any live execution.

## Run

Install dependencies from `requirements.txt`, configure any optional API keys as environment variables, then run:

```bash
python main.py
```

Keep the bot in paper mode while validating signal quality, net P/L, drawdown, trade count, and API reliability.
