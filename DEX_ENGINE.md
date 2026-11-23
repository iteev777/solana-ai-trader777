# Trojan V2 DEX route engine

## Current capability

`dex_engine.py` is the multi-pool quote-selection layer behind the authenticated dashboard endpoints:

- `GET /api/dex-status` — router, on-chain protocol and execution-lock status.
- `GET /api/dex-route` — compare Jupiter, Raptor and one or more direct pool candidates.
- `pool_address` accepts a comma-separated list, for example `pool_address=POOL_A,POOL_B,POOL_C`.

The engine asks the aggregator comparison layer once, then quotes each supplied direct pool against its verified on-chain program owner. Direct-pool quotes are independent candidates, not assumed to be the same pool family because of a discovery label.

## Route ranking

Every candidate must have a positive output and minimum output, matching output mint, known price impact, acceptable route quality, acceptable hop count and fresh quote/snapshot. Invalid or stale routes are rejected with a reason.

Ranking uses the minimum output in raw units of the requested output mint. DEX-level pool/router fees are expected to be reflected by the underlying quote adapters. Network and priority fees are not safely comparable when denominated in SOL and the output is another token; they are only deducted when an explicit estimate is supplied in output-token raw units through the Python API's `network_fee_output_amounts` argument. The dashboard endpoint does not guess that conversion, and its response warns when network costs are unpriced.

## Direct-pool adapter coverage

The on-chain decoder recognizes 11 pool families. The active direct quote adapter list is narrower and is reported by `GET /api/route-status` / `GET /api/dex-status`. A decoder is not treated as an executable quote adapter. Unsupported or incomplete pool families fail closed instead of receiving fabricated quotes.

## Safety boundary

This engine is read-only. It does not construct protocol-specific swap instructions, sign transactions, send transactions, or claim live readiness. Live execution remains locked. Before any live release, each protocol requires separately audited instruction/account construction, transaction simulation, slippage and fee validation, signer isolation, idempotent send/retry handling, confirmation/finality tracking and failure recovery. A quote-selection PR alone does not establish that those requirements are met.