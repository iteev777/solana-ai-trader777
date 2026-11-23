"""Central, fail-closed capability inventory for Trojan's Solana DEX support.

A quote adapter is not a transaction builder. A builder is not proof of live
execution. This registry keeps those capabilities separate in API/dashboard
status and defaults unknown protocols to unsupported.
"""
import importlib


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
    "raydium_cpmm": ("raydium_cpmm_builder", "build_raydium_cpmm_swap", "simulate_raydium_cpmm_transaction"),
    "raydium_amm_v4": ("raydium_amm_v4_builder", "build_raydium_amm_v4_swap", "simulate_raydium_amm_v4_transaction"),
    "fluxbeam_amm": ("fluxbeam_builder", "build_fluxbeam_swap", "simulate_fluxbeam_transaction"),
    "moonit_amm": ("moonit_builder", "build_moonit_swap", "simulate_moonit_transaction"),
    "pumpswap_v2": ("pumpswap_v2_builder", "build_pumpswap_v2_swap", "simulate_pumpswap_v2_transaction"),
    "orca_whirlpool": ("orca_whirlpool_builder", "build_orca_whirlpool_swap", "simulate_orca_whirlpool_transaction"),
    "meteora_dlmm": ("meteora_dlmm_builder", "build_meteora_dlmm_swap", "simulate_meteora_dlmm_transaction"),
}


def _capability(module_name, function_name):
    try:
        module = importlib.import_module(module_name)
        present = callable(getattr(module, function_name, None))
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        present = False
    return {
        "available": present,
        "module": module_name,
        "entrypoint": function_name,
        "status": "ENTRYPOINT_AVAILABLE" if present else "UNAVAILABLE_ENTRYPOINT_MISSING",
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

    for name, (module, function, simulation_function) in NATIVE_BUILDERS.items():
        builder = _capability(module, function)
        simulation = _capability(module, simulation_function)
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
        # Builder presence does not prove that the protocol-specific RPC
        # simulation entrypoint is present or callable.
        entry["simulation"] = simulation
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
