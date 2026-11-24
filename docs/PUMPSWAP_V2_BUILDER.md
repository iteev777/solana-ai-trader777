# PumpSwap V2 native unsigned swap builder

Trojan prepares native PumpSwap V2 transactions for two exact-input directions:

- Quote-token input: `buy_exact_quote_in_v2(spendable_quote_in, min_base_amount_out)`
- Base-token input: `sell_v2(base_amount_in, min_quote_amount_out)`

The instruction discriminators and 17-account order are taken from the current
official PumpSwap IDL. The builder does not use a hand-invented account list.

## Preparation flow

1. GET `/api/pumpswap-v2-quote` with `pool_address`, `input_mint`,
   `amount` in raw token units and optional `slippage_bps` (0–300).
2. POST `/api/pumpswap-v2-order` with `X-Trojan-Action: 1` and JSON containing
   the complete quote, wallet public key, existing user base token account and
   existing user quote token account.
3. Trojan reads and validates the PumpSwap pool and its canonical PDA, then
   independently obtains a fresh confirmed quote. The minimum output used in
   instruction data comes from that fresh quote, not a client-supplied number.
4. It checks the GlobalConfig, FeeConfig, pool vaults, mints, user token
   accounts, current buyback recipient list and an existing valid buyback ATA.
   It derives the user volume accumulator and event authority PDAs.
5. It compiles the 17-account V2 instruction into an unsigned legacy
   transaction and requires a successful Solana RPC simulation before
   returning the transaction.

## Deliberate limitations

- Legacy SPL Token and the audited Token-2022 subset are supported. Token-2022
  `TransferFeeConfig` is accepted with epoch-aware quote math; supported metadata
  extensions are allowed, and the corresponding `TransferFeeAmount` plus
  `ImmutableOwner` token-account extensions are validated. Transfer hooks,
  confidential transfers, pausable/non-transferable behavior and all other
  unmodelled mint/account extensions are refused.
- Current direct quote support refuses mayhem, cashback and holder-reward
  pools. Cashback coins are specifically not supported by PumpSwap V2.
- User base and quote token accounts must already exist and belong to the
  signing wallet.
- The selected buyback recipient's quote ATA must already exist and match the
  live GlobalConfig recipient list. The builder does not create ATAs.
- A missing user volume accumulator is permitted because the on-chain
  instruction initializes it if needed; an existing accumulator is validated.
- The server never signs or receives private keys. A separate default-off,
  single-use external-signature broadcast gate is documented in
  `docs/NATIVE_EXECUTION.md`; it requires `TROJAN_NATIVE_EXECUTE_ENABLED=true`
  and explicit confirmation. The native broadcast path is hard locked in code;
  setting `TROJAN_NATIVE_EXECUTE_ENABLED=true` cannot unlock it. The separate
  Jupiter V2 `/execute` adapter is also hard locked in code; no environment
  variable or request confirmation can unlock it. The repository-wide live-path
  audit remains a release blocker.
- A successful simulation is not a guarantee of later inclusion or execution;
  pool state may change after simulation and on-chain min-out remains the final
  price guard.

## API example

Quote:

```http
GET /api/pumpswap-v2-quote?pool_address=<POOL>&input_mint=<MINT>&amount=1000000&slippage_bps=100
```

Prepare and simulate:

```http
POST /api/pumpswap-v2-order
Content-Type: application/json
X-Trojan-Action: 1

{
  "quote": { "...": "complete quote response" },
  "wallet": "<PUBLIC_WALLET>",
  "user_base_token_account": "<EXISTING_BASE_ATA>",
  "user_quote_token_account": "<EXISTING_QUOTE_ATA>"
}
```

A successful response contains the unsigned transaction, exact instruction
data, selected buyback recipient, required signer, last valid block height and
simulation report.
