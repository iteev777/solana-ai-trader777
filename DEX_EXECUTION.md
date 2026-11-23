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


## Token-2022 extension inspection

The authenticated read-only endpoint `POST /api/token2022-inspect` accepts a
mint public key and returns the mint's Token-2022 base fields and parsed TLV
extension list. It validates the account owner, mint discriminator, zero
padding, TLV bounds, duplicate extension types, and mint-vs-account extension
types. Known extensions are labeled with their protocol implications, including
transfer fees, confidential transfers, permanent delegates, transfer hooks,
non-transferability, pausable mints, and scaled UI amounts.

**Inspection is not Token-2022 swap support.** The response explicitly keeps
`swap_eligibility` blocked and `execution_enabled` false. Native builders and
wallet preflight still require the legacy SPL Token program. Transfer-fee
extensions change net received amounts and need epoch-aware fee calculations;
confidential transfers need client-side proof flows; transfer hooks require
additional account/program handling. Token-2022 ATA sizing also depends on the
mint's required account extensions, so the legacy 165-byte ATA path must not be
used for these mints. See the official
[Solana Token-2022 documentation](https://solana.com/docs/tokens) and
[transfer-fee extension guide](https://solana.com/docs/tokens/extensions/transfer-fees).

## Non-custodial ATA creation order

When the wallet does not yet have its canonical legacy SPL token account, call
`POST /api/ata-order` with dashboard authentication, `X-Trojan-Action: 1`,
and JSON containing `wallet` and `mint` (both public keys). The endpoint
checks the mint owner/layout, derives the canonical ATA, checks wallet SOL,
fetches the exact 165-byte token-account rent exemption and message fee, builds
an Associated Token Program `CreateIdempotent` instruction, and simulates the
unsigned transaction. The response contains the unsigned base64 transaction,
ATA address, rent/fee/reserve breakdown, and simulation result. The connected
wallet must review and sign it locally; this endpoint never signs or broadcasts.

If the ATA already exists and validates, the endpoint reports
`ATA_ALREADY_EXISTS` and returns no transaction. Token-2022 mints are rejected.
The order is a separate user-approved transaction; after it confirms, rerun
wallet preflight and create the swap order. Rent and fees are estimates from
confirmed RPC state and can change before signing; the wallet must review the
transaction and current balance. This is not evidence of mainnet end-to-end
execution.

## Read-only wallet preflight

Before requesting a native Raydium CPMM or PumpSwap order, the dashboard can
check the wallet and token accounts using one confirmed
`getMultipleAccounts` snapshot.

```http
POST /api/wallet-preflight
Content-Type: application/json
X-Trojan-Action: 1

{
  "wallet": "<PUBLIC_WALLET_ADDRESS>",
  "input_mint": "<INPUT_MINT>",
  "output_mint": "<OUTPUT_MINT>",
  "input_token_account": "<EXISTING_INPUT_TOKEN_ACCOUNT>",
  "output_token_account": "<EXISTING_OUTPUT_TOKEN_ACCOUNT>",
  "input_amount": "1000000"
}
```

The preflight validates the wallet account, legacy SPL Token mint layouts,
token-account mint and authority, initialized state, raw input balance, and a
configurable minimum SOL reserve. It also derives the canonical input and output
Associated Token Account (ATA) addresses and reports whether each supplied
account is that wallet/mint ATA. Valid non-ATA token accounts remain accepted;
the native builders can use explicitly supplied token accounts. The reserve defaults to 10,000,000 lamports
(0.01 SOL) and can be changed with `TROJAN_MIN_SOL_RESERVE_LAMPORTS`.

A missing output token account is reported with
`CREATE_OUTPUT_TOKEN_ACCOUNT_AND_RETRY`, alongside the canonical output ATA
address; Trojan does not create it or spend SOL. This is an ATA discovery and
readiness check, not an ATA creation manager. Token-2022 is explicitly unsupported by this native preflight because
the current native builders require the legacy SPL Token program.

This is a funding/account readiness check, not an exact fee or rent quote. The
response explicitly reports `network_fee_estimate: NOT_ESTIMATED`. It is
read-only: it does not sign, build, create accounts, or broadcast transactions.
A READY result only means the checked prerequisites passed at the returned
confirmed slot; balances and account state can change immediately afterward.

## Multi-route DEX ranking confidence

The read-only multi-pool route endpoint compares eligible Jupiter, Raptor and
verified direct-pool quotes. It ranks routes using minimum output after
subtracting only explicitly supplied network-fee estimates denominated in the
same output mint.

The response exposes:
- `ranking_confidence: COST_ADJUSTED` when every eligible route has a
  comparable output-mint network-fee estimate;
- `ranking_confidence: PROVISIONAL_UNPRICED_NETWORK_COSTS` when one or more
  eligible routes lack that estimate;
- `ranking_is_provisional`, `unpriced_eligible_route_count`, and
  `unpriced_eligible_route_keys` to identify the affected comparison;
- `ranking_note` to explain whether the selected route is provisional.

A provisional selection is only the best result under currently available
cost data; unpriced fees may change the route order. Trojan does not invent
fee conversions or present an incomplete comparison as cost-adjusted. Route
selection remains read-only and does not authorize signing or broadcasting.

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
- This adapter uses Jupiter's meta-aggregator order path. Native
  Raydium CPMM and PumpSwap V2 builders are documented in
  `docs/RAYDIUM_CPMM_BUILDER.md` and `docs/PUMPSWAP_V2_BUILDER.md`.
  Both prepare and simulate unsigned legacy transactions. The server never
  signs; a separate single-use external-signature broadcast endpoint is gated
  by `TROJAN_NATIVE_EXECUTE_ENABLED` (default false) and explicit confirmation.
  See `docs/NATIVE_EXECUTION.md`. Builders for other DEX protocols remain
  follow-up work.

## Environment

- `JUPITER_API_KEY`: optional for keyless development, recommended for production.
- `TROJAN_JUPITER_V2_BASE_URL`: defaults to `https://api.jup.ag/swap/v2`.
- `TROJAN_JUPITER_EXECUTE_ENABLED`: defaults to `false`; do not enable until
  wallet signing, paper tests, simulation, and risk controls have been validated.
