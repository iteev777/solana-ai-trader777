# Native transaction preparation and execution lock

Trojan can prepare and simulate unsigned legacy transactions for a restricted
set of native Solana pool builders: Raydium CPMM, PumpSwap V2, Orca Whirlpool
and Meteora DLMM. Each builder validates protocol-specific account state and
requires a fresh quote plus a successful unsigned RPC simulation before a
single-use approval is registered.

## Current safety state

**Broadcast is hard locked in code.** The `TROJAN_NATIVE_EXECUTE_ENABLED`
environment variable is not sufficient to enable a real transaction. The
`NATIVE_BROADCAST_HARD_LOCK` constant deliberately forces
`broadcast_available=false` until a separately reviewed live-trading release.
Do not remove this lock during routine development.

- No server private key or seed phrase is accepted.
- The server does not sign transactions.
- The server does not broadcast native transactions while the hard lock is set.
- Unsigned transaction preparation and simulation are not proof of a successful
  signed transaction.
- Approval records are currently persisted in the local SQLite database and
  consumed atomically as single-use records. They survive a process restart on
  the same deployment, but they are not shared across multiple replicas or
  deployments. A future multi-replica release requires shared transactional
  storage with atomic consume, expiry, replay protection and restart recovery;
  sticky routing alone is not the preferred long-term design.
- A successful simulation is not a guarantee of execution or profitability.

## Supported subset and limitations

| Builder | Instruction | Important limits |
|---|---|---|
| Raydium CPMM | SwapBaseInput | Legacy SPL Token; existing user token accounts |
| PumpSwap V2 | buy_exact_quote_in_v2 / sell_v2 | Legacy SPL Token; standard non-cashback/non-mayhem pool subset; existing buyback ATA |
| Orca Whirlpool | Swap | Legacy SPL Token; supported account/tick-array subset |
| Meteora DLMM | Swap2 | Legacy SPL Token; restricted initialized bin-array discovery; not full bitmap traversal |

Token-2022 transfer fees/hooks, automatic ATA creation and unsupported pool
modes must be rejected rather than silently treated as supported. Meteora DLMM
currently has a restricted bin-array window; fragmented or wider liquidity can
remain unsupported.

## Readiness API

Authenticated dashboard endpoints expose:
- `GET /api/native-execution-status` — code hard-lock and signing/broadcast status.
- `GET /api/readiness` — paper readiness and integration health.
- `GET /api/protocol-validation` — observed RPC decode attempts and results.

A protocol with zero real decode attempts is unobserved, not validated. Unit
tests do not count as mainnet protocol validation.

## Future release gate

Before the code hard lock can be removed, require a separately reviewed change
that demonstrates a devnet end-to-end transaction using an external wallet,
signature-verifying simulation, safe submission, confirmation/finality
tracking, expiry/replay protection, durable approval handling where needed,
Token-2022 decisions, and explicit user approval. Mainnet live trading is not
part of this readiness task.
