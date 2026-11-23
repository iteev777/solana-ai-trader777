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
