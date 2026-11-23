use anyhow::{Context, Result};
use serde::Deserialize;
use solana_client::nonblocking::rpc_client::RpcClient;
use solana_sdk::{commitment_config::CommitmentConfig, pubkey::Pubkey};
use std::{env, fs, str::FromStr, time::{Duration, Instant}};

#[derive(Deserialize)]
struct Input { account_keys: Vec<String> }

fn percentile(values: &[f64], pct: f64) -> Option<f64> {
    if values.is_empty() { return None; }
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let idx = (((sorted.len() - 1) as f64) * pct).ceil() as usize;
    Some((sorted[idx.min(sorted.len() - 1)] * 100.0).round() / 100.0)
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    let input_path = args.get(1).context("usage: benchmark <benchmark_accounts.json>")?;
    let input: Input = serde_json::from_slice(&fs::read(input_path)?)?;
    anyhow::ensure!(!input.account_keys.is_empty(), "empty account key set");
    anyhow::ensure!(input.account_keys.len() <= 50, "common workload exceeds ScreenerBot's 50-account batch");
    let keys: Vec<Pubkey> = input.account_keys.iter()
        .map(|s| Pubkey::from_str(s).with_context(|| format!("invalid pubkey: {s}")))
        .collect::<Result<_>>()?;
    let url = env::var("SOLANA_RPC_URL").ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "https://api.mainnet-beta.solana.com".to_string());
    let client = RpcClient::new_with_commitment(url, CommitmentConfig::confirmed());
    let rounds = 12usize;
    let mut samples = Vec::new();
    let mut successful = 0usize;
    let mut errors = Vec::new();

    // Two warm-up calls are excluded, then 12 measured cycles at the same
    // 500 ms cadence used by ScreenerBot's account fetcher.
    for index in 0..(rounds + 2) {
        let started = Instant::now();
        match client.get_multiple_accounts(&keys).await {
            Ok(accounts) => {
                let elapsed = started.elapsed().as_secs_f64() * 1000.0;
                if accounts.len() == keys.len() && accounts.iter().all(|a| a.is_some()) {
                    successful += 1;
                }
                if index >= 2 { samples.push(elapsed); }
            }
            Err(error) => errors.push(format!("{error:#}")),
        }
        if index < rounds + 1 {
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }
    let mean = if samples.is_empty() { None } else {
        Some((samples.iter().sum::<f64>() / samples.len() as f64 * 100.0).round() / 100.0)
    };
    let min = samples.iter().copied().reduce(f64::min)
        .map(|x| (x * 100.0).round() / 100.0);
    let max = samples.iter().copied().reduce(f64::max)
        .map(|x| (x * 100.0).round() / 100.0);
    let output = serde_json::json!({
        "benchmark": "screenerbot-rust-solana-client-getMultipleAccounts",
        "pool_protocol_scope": "same Raydium CPMM account set derived by Trojan",
        "account_count": keys.len(),
        "batch_limit_accounts": 50,
        "rounds": rounds,
        "interval_ms": 500,
        "samples": samples.len(),
        "p50_ms": percentile(&samples, 0.50),
        "p95_ms": percentile(&samples, 0.95),
        "mean_ms": mean,
        "min_ms": min,
        "max_ms": max,
        "successful_full_account_responses": successful,
        "attempts_including_warmups": rounds + 2,
        "errors": errors.iter().take(5).collect::<Vec<_>>(),
        "note": "This uses the same solana-client 2.1.0 nonblocking RpcClient and confirmed get_multiple_accounts call used by ScreenerBot's fetcher; it isolates RPC fetch performance, not the entire ScreenerBot application loop."
    });
    println!("{}", serde_json::to_string_pretty(&output)?);
    fs::write("rust-rpc-benchmark.json", serde_json::to_vec_pretty(&output)?)?;
    anyhow::ensure!(!samples.is_empty(), "Rust RPC benchmark produced no successful timed samples");
    Ok(())
}
