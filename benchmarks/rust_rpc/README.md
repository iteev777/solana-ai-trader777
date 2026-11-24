# Rust RPC sidecar

Trojan can optionally route batched `getMultipleAccounts` reads through a persistent
Rust `solana-client` process. The normal Python transport remains the default and
is used automatically if the sidecar is not configured or fails.

## Build

From the repository root, in the same runtime image that runs Trojan:

```bash
cargo build --release --manifest-path benchmarks/rust_rpc/Cargo.toml --bin rpc_sidecar
```

Set the environment variable to the resulting executable path:

```bash
TROJAN_RUST_RPC_BIN=/absolute/path/to/target/release/rpc_sidecar
```

Keep the existing `SOLANA_RPC_URL` / `SOLANA_RPC_URLS` configuration. The Python
transport starts one long-lived sidecar per RPC endpoint, reuses its Rust
`RpcClient`, and continues to use the established Python endpoint rotation,
rate-limit cooldown and fallback behavior. Only confirmed, read-only
`getMultipleAccounts` calls are routed through Rust; other RPC methods remain
on the existing transport.

## Verify

```bash
python benchmarks/pool_rpc_parity.py
TROJAN_RUST_RPC_BIN=benchmarks/rust_rpc/target/release/rpc_sidecar \
  python benchmarks/rust_rpc/sidecar_smoke.py
```

The smoke test reuses the exact account-key workload produced by the first
benchmark and checks account count plus confirmed context slot. CI builds the
binary and uploads the measured sidecar results.

## Safety and rollout

This is an opt-in read-only optimization. Do not enable it in production until
the CI build and sidecar integration benchmark pass in the target runtime.
Live transaction signing and live trading are unrelated and remain disabled.
