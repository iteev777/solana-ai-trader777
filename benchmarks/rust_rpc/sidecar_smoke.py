#!/usr/bin/env python3
"""Exercise Trojan's optional persistent Rust transport against the shared live account set."""
import json
import os
import statistics
import time

from rpc_transport import rpc_call

with open("benchmark_accounts.json", encoding="utf-8") as handle:
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
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * value + 0.999999))], 2)

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
    "note": "This is a transport integration check on the exact account list used by the paired benchmark; it does not enable or execute live trading."
}
with open("rust-sidecar-benchmark.json", "w", encoding="utf-8") as handle:
    json.dump(output, handle, indent=2)
print(json.dumps(output, indent=2))
