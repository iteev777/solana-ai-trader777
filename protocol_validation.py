"""Persistent mainnet decoder validation telemetry for supported Solana pool protocols."""

SUPPORTED = (
    "pumpswap", "pumpfun_legacy", "raydium_amm_v4", "raydium_cpmm",
    "raydium_clmm", "orca_whirlpool", "meteora_dlmm", "meteora_damm_v2",
    "meteora_dbc", "fluxbeam_amm", "moonit_amm",
)


def _normalize(value):
    try:
        from solana_onchain import normalize_protocol
        return normalize_protocol(value)
    except Exception:
        return str(value or "unknown").strip().lower().replace("-", "_")


def record_validation(declared_protocol, pool_address, mint, snapshot=None, error=None):
    protocol = _normalize(declared_protocol)
    import pool_analysis
    success = error is None and isinstance(snapshot, dict) and snapshot.get("onchainVerified") is True
    if error is not None:
        failure_class = pool_analysis.classify_pool_error(error)
        if failure_class == "transient":
            if pool_address:
                pool_analysis.record_transient_failure(pool_address, error)
            return {
                "protocol": protocol,
                "success": False,
                "failure_class": "transient",
                "recorded": False,
            }
        if pool_address:
            pool_analysis.record_structural_failure(pool_address, error)
    elif success and pool_address:
        pool_analysis.record_success(pool_address)
    if success:
        source = str(snapshot.get("marketSource") or "")
        if source.startswith("solana_rpc_"):
            protocol = source[len("solana_rpc_"):]
    if not success and pool_address:
        try:
            from solana_onchain import PROGRAMS, _account
            owner = _account(pool_address).get("owner")
            protocol = next(
                (name for name, program_id in PROGRAMS.items() if program_id == owner),
                protocol
            )
        except Exception:
            pass
    if protocol not in SUPPORTED:
        protocol = "unknown"
    recorded = False
    try:
        import database
        database.record_protocol_validation(
            protocol, pool_address, mint, success,
            None if success else (type(error).__name__ if error is not None else "NO_VERIFIED_SNAPSHOT"),
            snapshot.get("observedAt") if success else None
        )
        recorded = True
    except Exception as db_error:
        print("[TROJAN V2] PROTOCOL VALIDATION TELEMETRY ERROR:", type(db_error).__name__, flush=True)
    return {"protocol": protocol, "success": success, "recorded": recorded}


def get_validation_summary():
    import database
    return database.get_protocol_validation_summary()
