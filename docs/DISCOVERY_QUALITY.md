# Discovery provenance and candidate-quality scoring

Trojan V2 keeps the raw discovery-feed labels for audit, but candidate consensus
scoring counts independent provider families rather than endpoint names. For
example, GeckoTerminal new-pool, trending-pool and token-pool feeds are one
provider family, not three independent confirmations. This prevents feed
multiplication from artificially inflating the consensus component of a score.

The quality report exposes both:
- `source_count`: number of distinct raw feed labels observed;
- `independent_source_count` and `source_families`: normalized provider-family
  evidence used by the score.

The score gives limited corroboration credit to one provider, more to two
independent families, and its full source-agreement component only at four or
more independent families. Unknown provenance remains explicit and does not
count as corroboration.

The scorer also rejects impossible observed values instead of treating them as
ordinary low activity: negative liquidity/volume/transaction counts, fractional
transaction counts, future pool-creation timestamps, invalid price and invalid
valuation. Missing values remain unknown and do not become fabricated zeroes.
A timestamp up to one minute in the future is tolerated for minor clock skew;
larger future timestamps are hard rejects.

These quality tiers are prioritisation and data-integrity signals, not a
standalone buy instruction. The candidate scorer still reports
`gating_enabled: false`; existing risk/security filters and strategy gates
remain responsible for entry eligibility. Run the full test suite with:

```bash
python -m unittest discover -s tests -v
```


## Feed reliability and multi-source merge

The token aggregator records per-feed success/failure counts, consecutive failures,
last error, result count and latency in SQLite. The last completed scan timestamp
and unique-token count are persisted as well, so the dashboard can report feed
health after a process restart. The legacy `pumpfun_scanner.py` module actually calls Birdeye's `new_listing`
endpoint with `meme_platform_enabled=true`; it is not an official Pump.fun API
feed. It is now attributed to the Birdeye provider family, so Birdeye's meme
listing and token-list feeds cannot create two independent consensus votes.

When multiple feeds return the same mint, later feeds can fill missing metadata,
replace placeholder strings and replace non-positive numeric market metrics with
positive observations. Existing positive values are retained rather than being
overwritten blindly. Raw source provenance is merged so consensus scoring still
counts independent provider families, not endpoint count.


## Discovery source controls

The discovery runner supports a global switch and provider/feed switches. Values
`1`, `true`, `yes` and `on` enable a feed; `0`, `false`, `no` and `off` disable
it. All source switches default to enabled. They are read when the Python process
imports the discovery aggregator, so restart the process after changing them.

| Variable | Controls |
|---|---|
| `TROJAN_DISCOVERY_ENABLED` | Entire discovery cycle |
| `TROJAN_GECKOTERMINAL_ENABLED` | All GeckoTerminal discovery feeds |
| `TROJAN_GECKOTERMINAL_NEW_POOLS_ENABLED` | GeckoTerminal new pools |
| `TROJAN_GECKOTERMINAL_TRENDING_ENABLED` | GeckoTerminal trending pools |
| `TROJAN_GECKOTERMINAL_RECENTLY_UPDATED_ENABLED` | GeckoTerminal recently updated tokens |
| `TROJAN_BIRDEYE_MEME_LISTING_ENABLED` | Birdeye meme-platform new listings (legacy module path: `pumpfun_scanner.py`) |
| `TROJAN_BIRDEYE_ENABLED` | Birdeye token-list discovery |

Existing provider-level and per-feed controls for DexScreener, RugCheck, Jupiter,
CoinGecko and DeFiLlama are also supported by the aggregator. The old `TROJAN_PUMPFUN_ENABLED` variable remains a fallback alias for
`TROJAN_BIRDEYE_MEME_LISTING_ENABLED` to preserve existing deployments. Disabling one
provider does not disable other providers. A provider failure is recorded against
that feed and does not discard successful results from other feeds.
