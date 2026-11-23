use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};
use solana_client::nonblocking::rpc_client::RpcClient;
use solana_sdk::{commitment_config::CommitmentConfig, pubkey::Pubkey};
use std::{io::{self, BufRead, Write}, str::FromStr, time::Duration};

#[tokio::main]
async fn main() {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let response = match line {
            Ok(line) => match serde_json::from_str::<Value>(&line) {
                Ok(input) => handle(input).await,
                Err(error) => json!({"ok": false, "error": format!("invalid request: {error}")}),
            },
            Err(error) => json!({"ok": false, "error": format!("stdin error: {error}")}),
        };
        if writeln!(stdout, "{}", response).is_err() { break; }
        if stdout.flush().is_err() { break; }
    }
}

async fn handle(input: Value) -> Value {
    match execute(input).await {
        Ok(result) => json!({"ok": true, "result": result}),
        Err(error) => json!({"ok": false, "error": error}),
    }
}

async fn execute(input: Value) -> Result<Value, String> {
    let url = input.get("url").and_then(Value::as_str)
        .filter(|s| !s.is_empty()).ok_or("missing RPC URL")?;
    let method = input.get("method").and_then(Value::as_str).ok_or("missing method")?;
    if method != "getMultipleAccounts" {
        return Err(format!("unsupported Rust sidecar method: {method}"));
    }
    let params = input.get("params").and_then(Value::as_array).ok_or("params must be an array")?;
    let keys_json = params.first().and_then(Value::as_array).ok_or("account keys must be an array")?;
    if keys_json.is_empty() || keys_json.len() > 100 {
        return Err("account key count must be between 1 and 100".to_string());
    }
    let keys: Vec<Pubkey> = keys_json.iter().map(|value| {
        let text = value.as_str().ok_or("account key must be a string")?;
        Pubkey::from_str(text).map_err(|error| format!("invalid account key: {error}"))
    }).collect::<Result<_, _>>()?;
    let client = RpcClient::new_with_timeout_and_commitment(
        url.to_string(), Duration::from_secs(8), CommitmentConfig::confirmed()
    );
    let response = client.get_multiple_accounts_with_commitment(
        &keys, CommitmentConfig::confirmed()
    ).await.map_err(|error| error.to_string())?;
    let values: Vec<Value> = response.value.into_iter().map(|account| match account {
        None => Value::Null,
        Some(account) => json!({
            "lamports": account.lamports,
            "owner": account.owner.to_string(),
            "data": [STANDARD.encode(account.data), "base64"],
            "executable": account.executable,
            "rentEpoch": account.rent_epoch
        }),
    }).collect();
    Ok(json!({"context": {"slot": response.context.slot}, "value": values}))
}
