# Raydium CPMM native unsigned swap builder

Trojan can now prepare a native Raydium CPMM exact-input swap instruction and
compile it into an unsigned legacy Solana transaction. The builder is a
non-custodial preparation path only: it does not sign, send, or confirm swaps.

## Flow

1. GET `/api/raydium-cpmm-quote` with `pool_address`, `input_mint`,
   `amount` in raw token units, and optional `slippage_bps` (0–300).
2. POST `/api/raydium-cpmm-order` with `X-Trojan-Action: 1` and a JSON body
   containing the quote, public wallet address, existing input token account
   and existing output token account.
3. Trojan independently re-quotes the exact pair/amount from confirmed on-chain
   state and uses that fresh minimum output. It does not trust a client-edited
   minimum-output value.
4. Trojan re-reads the pool, AmmConfig, vaults, mints, user token accounts and
   observation account in one confirmed RPC context. It checks account owners,
   pool status/open time, quote slot age, token account mint/owner/state and
   input balance.
5. Trojan builds the Raydium Anchor `SwapBaseInput` instruction, compiles a
   legacy transaction with one zeroed signature slot, and simulates it with
   Solana RPC. A failed or malformed simulation fails closed and the unsigned
   transaction is not returned.

## Safety boundaries

- Only the legacy SPL Token program is currently accepted. Token-2022, transfer
  fee extensions, transfer hooks and other extension behavior are refused until
  quote and transaction semantics are implemented and tested together.
- Both user token accounts must already exist and belong to the supplied public
  wallet. This endpoint does not create ATAs or silently spend rent.
- The quote must be recent relative to the final account snapshot (maximum 32
  slots by default); the quote is independently refreshed before construction.
- The wallet signs externally. No private key enters the Trojan server.
- The server never signs. A separate default-off, single-use external-signature
  broadcast gate is documented in `docs/NATIVE_EXECUTION.md`; it requires
  `TROJAN_NATIVE_EXECUTE_ENABLED=true` and explicit confirmation. Live execution
  remains disabled by default.
- The returned legacy transaction is a preparation artifact, not proof that a
  wallet will accept it or that a later network submission will land. The pool
  can change after simulation; the on-chain minimum-output check remains the
  final slippage guard.

## API examples

Quote:

```http
GET /api/raydium-cpmm-quote?pool_address=<POOL>&input_mint=<MINT>&amount=1000000&slippage_bps=100
```

Prepare and simulate:

```http
POST /api/raydium-cpmm-order
Content-Type: application/json
X-Trojan-Action: 1

{
  "quote": { "...": "complete quote response" },
  "wallet": "<PUBLIC_WALLET>",
  "user_input_token_account": "<EXISTING_INPUT_ATA>",
  "user_output_token_account": "<EXISTING_OUTPUT_ATA>"
}
```

A successful response contains a base64 unsigned legacy transaction,
required signer public key, last valid block height and simulation report.
