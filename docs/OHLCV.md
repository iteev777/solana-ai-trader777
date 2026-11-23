# OHLCV integrity

The GeckoTerminal 1-minute candle feed is normalized before indicator calculation or persistence. The validator sorts rows, keeps the last valid row for duplicate timestamps, and rejects non-finite values, malformed OHLC geometry, negative volume, fractional timestamps and future timestamps.

The quality report includes input/valid/invalid/duplicate counts, missing-minute gap count, largest gap and latest candle age. Indicator snapshots fail closed when the newest candle is older than `TROJAN_OHLCV_MAX_AGE_SECONDS` (default 180 seconds) or a discontinuity exceeds `TROJAN_OHLCV_MAX_GAP_SECONDS` (default 300 seconds). Higher timeframes are not synthesized or forward-filled from missing minutes.

Both settings are bounded to avoid accidental unlimited staleness. Historical candles can be stored for analysis even when the live indicator snapshot is stale; persisted history does not make a stale snapshot strategy-ready.


## Persistence and retention

Normalized candles are upserted by pool address, timeframe and timestamp, so the latest valid observation replaces an earlier candle without duplicating its volume. Supported stored intervals are 1m, 3m, 5m, 15m, 30m, 1h and 4h. Historical rows are retained for 30 days by default; set `TROJAN_OHLCV_RETENTION_DAYS` to an integer from 1 to 3,650. The fetch worker prunes expired rows at most once per hour. A stale or discontinuous series may still be stored for history, but the corresponding live indicator snapshot remains not-ready.
