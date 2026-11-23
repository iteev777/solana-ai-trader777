# Trojan DEX execution adapter (non-custodial)

Trojan's Jupiter Swap API V2 adapter adds a wallet-signing handoff without storing
or loading a private key on the server.

## Flow

1. POST `/api/dex-order` with input/output mint, raw input amount, and the
   user's public wallet address. Trojan requests Jupiter `GET /order` and
   returns the unsigned base64 transaction plus its `request_id`.
2. The user's connected wallet signs the returned transaction locally.
3. Trojan simulates the unsigned transaction through Solana RPC (`sigVerify=false`). A PASS is required before the order is marked execution-eligible.
4. Only after the user explicitly approves the swap, POST the signed base64 transaction and `request_id` to `/api/dex-execute`. Jupiter `POST /execute` handles landing and confirmation.

## API

All POST requests require `X-Trojan-Action: 1`.

### Create an unsigned order

```http
POST /api/dex-order
Content-Type: application/json
X-Trojan-Action: 1

{
  "input_mint": "So11111111111111111111111111111111111111112",
  "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "amount": "100000000",
  "taker": "<PUBLIC_WALLET_ADDRESS>",
  "slippage_bps": 100,
  "simulate": true
}
```

The response includes `transaction`, `request_id`, `output_amount`,
`minimum_output_amount`, `router`, and route details. The transaction is
unsigned and must be signed by the user's wallet. Never send a private key.

### Execute a user-signed order

```http
POST /api/dex-execute
Content-Type: application/json
X-Trojan-Action: 1
X-Trojan-Confirm-Live: 1

{
  "signed_transaction": "<BASE64_SIGNED_TRANSACTION>",
  "request_id": "<REQUEST_ID_FROM_ORDER>"
}
```

## Safety gates

- Execution is **disabled by default**.
- Server-side execution additionally requires
  `TROJAN_JUPITER_EXECUTE_ENABLED=true`.
- The execute request additionally requires
  `X-Trojan-Confirm-Live: 1`.
- The API does not accept, store, or request a wallet private key.
- A recent successful unsigned RPC simulation is required. The in-memory request cache expires after 120 seconds and each simulation approval is consumed atomically by the first execute attempt. A failed or timed-out execute attempt requires a fresh order; this deliberately favors replay protection over automatic retries. The cache is process-local, so order and execute must reach the same app instance. Multi-replica deployments must use sticky routing or a shared durable approval store before enabling execution.
- Only a transaction returned by Jupiter `/order` can be executed with its cached `request_id`; do not modify the transaction message after order creation.
- The dashboard exposes `GET /api/dex-execution-status` for gate visibility.
- This adapter uses Jupiter's meta-aggregator order path. A separate native
  Raydium CPMM builder is documented in `docs/RAYDIUM_CPMM_BUILDER.md`.
  It builds and simulates unsigned legacy transactions only; no native signing
  or broadcast endpoint exists. Native builders for other DEX protocols remain
  follow-up work.

## Environment

- `JUPITER_API_KEY`: optional for keyless development, recommended for production.
- `TROJAN_JUPITER_V2_BASE_URL`: defaults to `https://api.jup.ag/swap/v2`.
- `TROJAN_JUPITER_EXECUTE_ENABLED`: defaults to `false`; do not enable until
  wallet signing, paper tests, simulation, and risk controls have been validated.
