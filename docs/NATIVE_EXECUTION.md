# Native signed transaction execution gate

Raydium CPMM and PumpSwap V2 order endpoints now register a single-use
approval only after their unsigned transaction has passed Solana RPC
simulation. The order response includes a random `request_id`, a 120-second
approval TTL and the SHA-256 digest of the exact message that was simulated.

## External wallet flow

1. Request and simulate a native order using the protocol-specific order API.
2. Sign the returned base64 legacy transaction in the user's external wallet.
3. POST `/api/native-execute` with `X-Trojan-Action: 1`,
   `X-Trojan-Confirm-Live: 1`, and JSON fields `request_id` and
   `signed_transaction`.

The server:
- requires `TROJAN_NATIVE_EXECUTE_ENABLED=true` (default: false);
- consumes the approval atomically before proceeding, so it cannot be replayed;
- verifies the signed transaction has exactly one non-zero external signature;
- compares the entire serialized message byte-for-byte with the approved
  simulated message;
- checks approval expiry and the transaction's last valid block height;
- re-simulates the signed transaction with signature verification enabled;
- checks approval expiry and blockhash validity again immediately before
  broadcasting with Solana RPC preflight enabled.

The response is `SUBMITTED_NOT_CONFIRMED`, not a confirmation claim. The
caller must independently track the returned signature to finality.

## Safety and deployment constraints

- The server never receives or stores a private key and never signs.
- No native transaction is broadcast unless the environment gate and explicit
  confirmation header are both present.
- The native approval cache is process-local. Order and execute must reach the
  same app instance. Multi-replica deployments need sticky routing or a shared
  durable, atomic approval store before enabling the environment gate.
- An approval is consumed even when later signature verification or simulation
  fails. The user must request a new quote/order.
- Keep the environment gate disabled until devnet end-to-end signing,
  simulation, submission and confirmation tests pass for each protocol.
- This gate does not enable the separate Jupiter execution adapter, which has
  its own environment and confirmation controls.
