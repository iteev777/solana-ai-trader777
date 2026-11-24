#!/usr/bin/env python3
"""Exercise Trojan's optional persistent Rust transport against the shared live account set."""
import json
import pathlib
import statistics
import sys
import time

# GitHub Actions may launch this script from benchmarks/rust_rpc rather than
# the repository root. Make the project modules importable in either case.
REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from rpc_transport import rpc_call


def main():
    accounts_path = pathlib.Path.cwd() / "benchmark_accounts.json"
    if not accounts_path.exists():
        accounts_path = REPOSITORY_ROOT / "benchmark_accounts.json"
    with accounts_path.open(encoding="utf-8") as handle:
        keys = json.load(handle)["account_keys"]

    samples = []
    present = 0
    slot = 0
    for index in range(14):
        started = time.perf_counter()
        result = rpc_call("getMultipleAccounts", [
            keys, {"encoding": "base64", "commitment": "confirmed"}
        ])
        elapsed = (time.perf_counter() - started) * 1000
        values = result.get("value") or []
        context = result.get("context") or {}
        if len(values) != len(keys):
            raise RuntimeError("Rust sidecar returned an unexpected account count")
        if not isinstance(context.get("slot"), int) or context["slot"] <= 0:
            raise RuntimeError("Rust sidecar returned no confirmed context slot")
        if index >= 2:
            samples.append(elapsed)
        present = sum(value is not None for value in values)
        slot = context["slot"]
        if index < 13:
            time.sleep(0.5)

    ordered = sorted(samples)

    def pct(value):
        index = min(len(ordered) - 1, int((len(ordered) - 1) * value + 0.999999))
        return round(ordered[index], 2)

    output = {
        "benchmark": "trojan-rust-sidecar-getMultipleAccounts",
        "account_count": len(keys),
        "samples": len(samples),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "mean_ms": round(statistics.mean(samples), 2),
        "successful_present_accounts": present,
        "confirmed_context_slot": slot,
        "transport": "persistent Rust solana-client sidecar via Trojan rpc_transport",
        "note": "Transport integration check only; it does not enable or execute live trading.",
    }
    output_path = pathlib.Path.cwd() / "rust-sidecar-benchmark.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
