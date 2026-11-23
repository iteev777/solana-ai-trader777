"""Central, fail-closed capability inventory for Trojan's Solana DEX support.

A quote adapter is not a transaction builder. A builder is not proof of live
execution. This registry keeps those capabilities separate in API/dashboard
status and defaults unknown protocols to unsupported.
"""
import importlib.util


QUOTE_ADAPTERS = {
    "pumpswap": ("solana_onchain", "quote_pumpswap_pool"),
    "pumpfun_legacy": ("solana_onchain", "quote_pumpfun_legacy_pool"),
    "raydium_cpmm": ("solana_onchain", "quote_raydium_cpmm_pool"),
    "raydium_clmm": ("raydium_clmm_quote", "quote_raydium_clmm_pool"),
    "raydium_amm_v4": ("solana_onchain", "quote_raydium_amm_v4_pool"),
    "orca_whirlpool": ("orca_whirlpool_quote", "quote_orca_whirlpool_pool"),
    "meteora_dlmm": ("meteora_dlmm_quote", "quote_meteora_dlmm_pool"),
    "meteora_damm_v2": ("meteora_damm_quote", "quote_meteora_damm_pool"),
    "meteora_dbc": ("meteora_dbc_quote", "quote_meteora_dbc_pool"),
    "fluxbeam_amm": ("solana_onchain", "quote_fluxbeam_pool"),
    "moonit_amm": ("solana_onchain", "quote_moonit_pool"),
}

NATIVE_BUILDERS = {
    "raydium_cpmm": ("raydium_cpmm_builder", "build_raydium_cpmm_swap"),
    "pumpswap_v2": ("pumpswap_v2_builder", "build_pumpswap_v2_swap"),
}


def _module_present(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _capability(module_name, function_name):
    present = _module_present(module_name)
    return {
        "available": present,
        "module": module_name,
        "entrypoint": function_name,
        "status": "IMPLEMENTED_MODULE_PRESENT" if present else "UNAVAILABLE_MODULE_MISSING",
    }


def get_dex_capabilities(decoded_protocols=None):
    """Return honest capability levels; never infer execution from quote support."""
    decoded = set(decoded_protocols or [])
    protocols = {}
    for name, (module, function) in QUOTE_ADAPTERS.items():
        quote = _capability(module, function)
        protocols[name] = {
            "pool_decoder_available": name in decoded,
            "quote": quote,
            "transaction_builder": {
                "available": False,
                "status": "NOT_IMPLEMENTED_FOR_THIS_PROTOCOL",
            },
            "simulation": False,
            "external_wallet_signing": False,
            "broadcast": False,
            "live_execution": False,
        }

    for name, (module, function) in NATIVE_BUILDERS.items():
        builder = _capability(module, function)
        protocol_name = "pumpswap" if name == "pumpswap_v2" else name
        entry = protocols.setdefault(protocol_name, {
            "pool_decoder_available": protocol_name in decoded,
            "quote": {"available": False, "status": "NO_QUOTE_ADAPTER_REGISTERED"},
            "transaction_builder": {},
            "simulation": False,
            "external_wallet_signing": False,
            "broadcast": False,
            "live_execution": False,
        })
        entry["transaction_builder"] = builder
        entry["builder_variant"] = name
        entry["simulation"] = bool(builder["available"])
        # Builders only create unsigned transactions. Simulation is separate
        # from broadcast and requires the dedicated simulation path.
        entry["external_wallet_signing"] = False
        entry["broadcast"] = False
        entry["live_execution"] = False

    return {
        "protocols": protocols,
        "quote_adapter_count": sum(
            1 for item in protocols.values()
            if item.get("quote", {}).get("available")
        ),
        "native_builder_count": sum(
            1 for item in protocols.values()
            if item.get("transaction_builder", {}).get("available")
        ),
        "execution_policy": "ALL_PROTOCOLS_LIVE_EXECUTION_DISABLED",
    }
