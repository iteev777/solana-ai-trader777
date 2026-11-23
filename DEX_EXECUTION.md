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

A related `POST /api/token2022-account-inspect` endpoint accepts an
`account` public key and reports the Token-2022 token-account base fields,
raw balance, initialized/frozen state, and account-side TLV extensions. Both
inspection endpoints are diagnostic only; they do not imply that the account
can be used by any current native swap builder.

**Inspection is not Token-2022 swap support.** The response explicitly keeps
`swap_eligibility` blocked and `execution_enabled` false. Most native builders and wallet preflight still require the legacy SPL Token
program. FluxBeam has a deliberately narrow Token-2022 path for zero-fee mints
without transfer hooks; non-zero transfer fees and unsupported extensions are
rejected. Transfer-fee extensions change net received amounts and need epoch-aware fee calculations;
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
checks that the payer exists as a standard system-owned wallet account
(with no account data), validates the mint owner/layout, derives the canonical
ATA, checks wallet SOL, fetches the exact 165-byte token-account rent exemption
and message fee, builds an Associated Token Program `CreateIdempotent`
instruction, and simulates the unsigned transaction. The response contains the unsigned base64 transaction,
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


## DEX protocol capability reporting

The dashboard engine status now exposes `protocol_capabilities` and
`capability_summary`. These fields deliberately separate pool decoding,
read-only quote adapters, native unsigned transaction builders, simulation,
external-wallet signing, broadcast, and live execution.

A protocol with a quote adapter is not thereby transaction-buildable. Current
native builder coverage includes Raydium CPMM, the supported standard PumpSwap
V2 pool mode, classic Orca Whirlpool, and the supported classic Meteora DLMM
Swap2 path. These builders are restricted to legacy SPL Token, require a fresh
quote and RPC simulation, and do not enable server signing, broadcast, or live
trading. Other quote adapters remain quote-only until their own
protocol-specific transaction layout, account validation, simulation, and
regression suite are implemented.

Unknown or unregistered protocols fail closed. The capability inventory is
descriptive; it does not grant execution permission.


## Orca Whirlpool native unsigned swap builder

Trojan now includes a classic Orca Whirlpool `Swap` instruction builder in
`orca_whirlpool_builder.py`. It uses the official Whirlpool Swap discriminator,
Borsh argument layout, and account order (legacy token program, external wallet
authority, Whirlpool, owner A/vault A, owner B/vault B, three tick arrays,
oracle). The builder re-quotes from confirmed on-chain state, checks quote-slot
freshness, validates the pool/vault/mint/user token accounts, and creates an
unsigned legacy transaction for external wallet signing.

This first version is deliberately restricted to static-fee classic Whirlpool
pools and legacy SPL Token. Adaptive-fee state, Token-2022, unsupported tick
array layouts, stale quotes, missing required accounts, and invalid account
ownership fail closed. The unsigned transaction can be RPC-simulated, but the
server does not sign or broadcast it. No mainnet end-to-end execution has been
established; live trading remains disabled.


The Orca endpoints are `GET /api/orca-whirlpool-quote` and authenticated
`POST /api/orca-whirlpool-order`. The order endpoint builds an unsigned
transaction, simulates it, and only then creates a short-lived approval for
the external-wallet flow. The native broadcast gate remains disabled by
default; Orca is only added to the protocol allow-list, not enabled for live
trading. The order endpoint requires `X-Trojan-Action: 1`.


## Meteora DLMM native unsigned Swap2 builder

Trojan includes a first native Meteora DLMM exact-input builder in
`meteora_dlmm_builder.py`. It uses the current `Swap2` instruction
(discriminator `414b3f4ceb5b5b88`), the 16-account IDL order, an empty
`RemainingAccountsInfo`, and the quote's initialized bin-array PDAs as
writable remaining accounts. Optional bitmap-extension and host-fee accounts
are omitted using the program-id sentinel; the memo program and event-authority
PDA are included in their IDL positions.

The builder independently requotes from confirmed state, validates the
LbPair, reserve vaults, legacy SPL Token mints and user token accounts, oracle,
optional bitmap extension, bin-array ownership/layout/PDA, input balance and
quote-slot freshness. It creates an unsigned legacy transaction and performs
an unsigned RPC simulation before registering a short-lived external-wallet
approval. The server never signs or broadcasts.

This initial path intentionally follows the current read-only quote adapter's
supported subset: legacy SPL Token only, `function_type=0`, no Token-2022
transfer-fee/hook handling, no limit-order fills, and at most three consecutive
initialized bin arrays. Pools or swaps outside those constraints fail closed.
The quote engine's limited bin-array discovery is not a substitute for the
full Meteora SDK bitmap traversal on wide/fragmented liquidity ranges; no
mainnet end-to-end signed swap has been established.

Endpoints:
- `GET /api/meteora-dlmm-quote`
- authenticated `POST /api/meteora-dlmm-order` with `X-Trojan-Action: 1`

Live execution remains locked. The native broadcast environment gate remains
off by default, and this builder's successful simulation is not proof that a
real wallet-signed transaction will land or confirm.


## Native direct-DEX builder coverage

Trojan now has native unsigned swap builders registered for all eleven direct
venues represented by the ScreenerBot Solana direct-swap venue set:

- Raydium CPMM, CLMM and AMM v4
- Orca Whirlpool
- Meteora DLMM, DAMM v2 and DBC
- Pump.fun Legacy and PumpSwap V2
- FluxBeam AMM
- Moonit AMM

Each builder re-quotes immediately before transaction construction, checks the
pool and token-account snapshot, validates the requested mint direction and
minimum output, and returns a wallet-signable transaction without server-side
signing or broadcasting. Pool-specific account layouts and unsupported modes
fail closed. Builder registration does not itself prove a successful mainnet
trade; protocol-specific Devnet and live-readiness tests remain separate.

Native transactions prepend Solana Compute Budget instructions. The current
default compute-unit limit is 300,000 and the default priority price is 10,000
micro-lamports per compute unit. The priority price can be adjusted with
`TROJAN_PRIORITY_FEE_MICROLAMPORTS` (integer range 0–1,000,000,000). The value
is returned in the unsigned order response so the wallet can review it before
signing. This is a configured fee policy, not a live network-fee oracle.

Live trading remains locked. The user must inspect and sign any transaction in
their own wallet; the bot does not hold wallet secrets.


## Token-2022 transfer-fee handling: Raydium CPMM

The native Raydium CPMM quote path now parses both Token-2022
TransferFeeConfig schedules and computes a conservative fee for the actual raw
transfer amount using both schedules' rounded fee and maximum-fee cap. This is
intentionally safer than selecting only the higher basis-point rate: the
schedule with the higher rate can have a lower cap, so the larger actual fee
must be chosen per amount.

The quote applies the input transfer fee before Raydium's trade/creator fees,
then applies the output transfer fee after any output-side creator fee. The
quoted output and slippage minimum are the net amount expected to arrive in the
user's token account. The unsigned CPMM builder re-reads the mint accounts and
accepts the same supported extension subset; unsupported mint extensions
(including active transfer hooks, confidential-transfer features, pausable,
non-transferable, and other unimplemented behavior) remain blocked.

This support is specific to Raydium CPMM and is not a blanket Token-2022
approval for the other ten direct DEX builders. Other venues retain their
existing restrictions until their quote and instruction semantics are audited
and tested individually. ATA creation for extension-sized Token-2022 accounts
is also a separate unfinished part of the Token-2022 project. Live signing and
broadcast remain disabled.


## Token-2022 ATA sizing

The non-custodial ATA order builder now accepts the supported Token-2022 mint
extension subset and derives the associated-token address using the mint's
actual token-program ID. Its rent query uses the required token-account data
length rather than assuming the legacy 165-byte layout: 170 bytes for a
Token-2022 account with the Associated Token Program's ImmutableOwner extension,
and 182 bytes when TransferFeeAmount is additionally required by a mint with
TransferFeeConfig. Existing Token-2022 ATAs are checked for the expected owner,
size, mint, authority and initialized state. Unsupported mint extensions remain
rejected; this is not general approval for every Token-2022 feature.

The ATA order is still unsigned and simulated only. The wallet must sign and
submit it externally; no account is created by the server.


## Token-2022 transfer-fee handling: Raydium CLMM

The native Raydium CLMM quote and SwapV2 builder now support the same
fee-aware Token-2022 subset as Raydium CPMM. Quotes apply the conservative
per-amount fee from both epoch schedules to the input before the tick walk and
to the output after the tick walk. Slippage minimums are calculated from the
net amount received by the wallet. Transfer-hook and other unsupported mint
extensions remain blocked.

The CLMM builder validates the mint's token program against its vault and
user token accounts, validates Token-2022 TransferFeeAmount and ImmutableOwner
account extensions, and places the actual token program for each mint side in
the SwapV2 account list. It also checks the token-program pair against both the
supplied quote and its fresh confirmed snapshot. This avoids constructing a
transaction with mismatched token-program accounts when a pool mixes legacy
SPL Token and Token-2022.

Token-2022 support is now implemented in the native Raydium CPMM and CLMM
paths only. Other DEX venues retain their explicit legacy-only or unsupported
checks until their own transfer-fee and account semantics are audited and
covered by tests. Live signing and broadcasting remain disabled.


## Token-2022 transfer-fee handling: Orca Whirlpool

The native Orca quote path now applies the conservative Token-2022 transfer
fee to the input before the Whirlpool tick walk and to the gross output after
the walk. The resulting quote and slippage minimum represent the net amount
received by the wallet. Transfer-hook and other unsupported mint extensions
remain rejected, and the existing adaptive-fee oracle restrictions are
unchanged.

The native transaction builder selects the classic Swap instruction for
legacy-only pools and SwapV2 when either side is Token-2022. For SwapV2 it
includes both mint-specific token programs, the memo program, both mint
accounts, and the None remaining-accounts option. It validates the fresh
quote's token-program pair against the transaction snapshot and checks the
Token-2022 account extensions. ScreenerBot's reference implementation notes
that its SwapV2 discriminator/account flow has not been independently
confirmed against a live mainnet transaction; Trojan's implementation is
therefore test/simulation-only until a signed, non-broadcast devnet or
controlled mainnet verification is performed.


## Token-2022 transfer-fee handling: Meteora DLMM

The native Meteora DLMM quote and Swap2 builder now support function_type=0
pools using the audited Token-2022 transfer-fee subset. The quote nets the input
transfer fee before the bin walk and nets the output transfer fee after the
bin walk; the wallet-facing minimum output is based on the net amount. Both
mint-specific token programs are checked against the reserve and wallet token
accounts and placed in the Swap2 account list. Transfer-hook and other
unsupported mint extensions remain rejected because RemainingAccountsInfo is
sent empty. The existing optional bitmap-extension and bin-array fingerprint
checks remain in place.

This does not enable every Meteora DLMM pool mode or arbitrary Token-2022
extensions. Only the explicitly validated function_type=0 path is included;
other modes and transfer-hook mints remain blocked. Signing and broadcasting
remain disabled.


## Token-2022 transfer-fee handling: PumpSwap V2

The native PumpSwap quote path now reads each mint's actual token-program owner,
validates both mint extension sets and both vault token-account layouts, and
applies a conservative transfer-fee allowance to the amount received by the
pool on buys and sells. It then applies the output mint's transfer fee after
PumpSwap's own trade fees, so the returned output and slippage minimum are net
of both fee layers. Unsupported extensions such as transfer hooks remain
fail-closed.

The unsigned V2 builder rechecks the quote's base/quote token-program pair
against the confirmed mint snapshot, validates the pool vaults, wallet ATAs,
and configured buyback-recipient quote ATA using the correct token program,
and places the base and quote token-program IDs in their respective V2
instruction account slots. Token-2022 ATAs use their extension-aware account
layouts; the builder does not assume 165 bytes for them. No account is created
by the swap builder, and no signing or broadcasting is performed.
