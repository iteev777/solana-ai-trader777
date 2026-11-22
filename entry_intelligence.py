"""Pre-entry Jupiter route and optional unsigned transaction simulation."""
import math
import os

WSOL_MINT = "So11111111111111111111111111111111111111112"


def analyze_entry_route(token_mint, trade_size_usd, slippage_bps=None):
    try:
        usd_size = float(trade_size_usd)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Trade size is invalid")
    if not math.isfinite(usd_size) or usd_size <= 0:
        raise ValueError("Trade size must be positive")

    from market_data import get_market_data
    sol_market = get_market_data(WSOL_MINT)
    sol_price = float((sol_market or {}).get("priceUsd") or 0)
    if not math.isfinite(sol_price) or sol_price <= 0:
        return {"allowed": False, "reason": "SOL_USD_PRICE_UNAVAILABLE"}

    lamports = int(round((usd_size / sol_price) * 1_000_000_000))
    if lamports <= 0:
        return {"allowed": False, "reason": "TRADE_SIZE_BELOW_ONE_LAMPORT"}

    import live_execution
    quote = live_execution.get_jupiter_quote(
        WSOL_MINT, token_mint, lamports, slippage_bps
    )
    quality = quote.get("route_quality") or live_execution.evaluate_route_quality(quote)
    if quality.get("status") != "PASS":
        return {
            "allowed": False, "reason": "JUPITER_ROUTE_REJECTED",
            "route_quality": quality,
            "quote": {
                "router": quote.get("router"),
                "price_impact_percent": quote.get("price_impact_percent"),
                "platform_fee_bps": quote.get("platform_fee_bps"),
                "quote_latency_ms": quote.get("quote_latency_ms"),
                "route_diagnostics": quote.get("route_diagnostics"),
            }
        }

    simulation = {"status": "SKIPPED", "reason": "PUBLIC_WALLET_OR_RPC_NOT_CONFIGURED"}
    status = live_execution.get_execution_status()
    if status.get("simulation_available"):
        simulation = live_execution.simulate_unsigned_swap(
            WSOL_MINT, token_mint, lamports, slippage_bps
        )
        if simulation.get("status") != "PASS":
            return {
                "allowed": False, "reason": "UNSIGNED_SWAP_SIMULATION_FAILED",
                "route_quality": quality, "simulation": simulation,
            }
    elif os.getenv("TROJAN_REQUIRE_UNSIGNED_SIMULATION", "false").lower() == "true":
        return {
            "allowed": False, "reason": "UNSIGNED_SIMULATION_REQUIRED_BUT_UNAVAILABLE",
            "route_quality": quality, "simulation": simulation,
        }

    return {
        "allowed": True, "reason": "JUPITER_ROUTE_AND_SIMULATION_APPROVED",
        "input_amount_lamports": lamports, "sol_price_usd": sol_price,
        "trade_size_usd": round(usd_size, 4),
        "route_quality": quality,
        "quote": {
            "router": quote.get("router"),
            "price_impact_percent": quote.get("price_impact_percent"),
            "platform_fee_bps": quote.get("platform_fee_bps"),
            "minimum_out_amount": quote.get("minimum_out_amount"),
            "out_amount": quote.get("out_amount"),
            "quote_latency_ms": quote.get("quote_latency_ms"),
            "route_diagnostics": quote.get("route_diagnostics"),
            "context_slot": quote.get("context_slot"),
        },
        "simulation": simulation,
    }
