# OHLCV integrity

The GeckoTerminal 1-minute candle feed is normalized before indicator calculation or persistence. The validator sorts rows, keeps the last valid row for duplicate timestamps, and rejects non-finite values, malformed OHLC geometry, negative volume, fractional timestamps and future timestamps.

The quality report includes input/valid/invalid/duplicate counts, missing-minute gap count, largest gap and latest candle age. Indicator snapshots fail closed when the newest candle is older than `TROJAN_OHLCV_MAX_AGE_SECONDS` (default 180 seconds) or a discontinuity exceeds `TROJAN_OHLCV_MAX_GAP_SECONDS` (default 300 seconds). Higher timeframes are not synthesized or forward-filled from missing minutes.

Both settings are bounded to avoid accidental unlimited staleness. Historical candles can be stored for analysis even when the live indicator snapshot is stale; persisted history does not make a stale snapshot strategy-ready.


## SQLite retention

Valid candles are persisted through the same OHLCV normalizer. Supported timeframes are limited to the known interval set. `TROJAN_OHLCV_RETENTION_DAYS` defaults to 30 days (1–3650), and the indicator fetch worker prunes expired rows at most once per hour. Historical rows can be stored even if the current indicator snapshot is stale; only the live indicator readiness gate controls strategy use.
