# ==========================================
# TROJAN V2 - MAIN TRADING ENGINE
# BATCH MARKET DATA OPTIMIZATION
# ==========================================

import time
import queue
import os
import math
from concurrent.futures import ThreadPoolExecutor

import paper_trading as paper
import sniper_scanner as sniper
import trading_manager as manager
import strategy as trading_strategy
import database

from scanner import scan_tokens
from discovery.token_aggregator import collect_tokens
from candidate_quality import rank_candidates, get_quality_status

from rugcheck import get_rugcheck_report
from liquidity_monitor import check_liquidity

from market_data import (
    get_market_data,
    get_market_data_batch,
    get_gecko_pool_data_batch
)
from indicators import fetch_pool_indicators
from solana_onchain import decode_pool_snapshot, get_onchain_status

from database import (
    initialize_database,
    save_analysis,
    get_trade_stats
)


# ==========================================
# CONFIGURATION
# ==========================================

WORKERS = 24
MAX_PENDING_TASKS = 500

# Rapid discovery and frequent paper-position checks.
SCAN_INTERVAL = 15
POSITION_CHECK_INTERVAL = 3
STATISTICS_INTERVAL = 30

MAX_RUGCHECK_SCORE = 40
SNIPER_MIN_LIQUIDITY = 2500

SNIPER_TOKEN_TIMEOUT = 300
DISCOVERY_TOKEN_TIMEOUT = 600

WAIT_RETRY_INTERVAL = 10
ERROR_RETRY_INTERVAL = 30
MAX_ANALYSIS_RETRIES = 2
REJECTED_TOKEN_RESCAN_INTERVAL = 60

executor = ThreadPoolExecutor(max_workers=WORKERS)
discovery_executor = ThreadPoolExecutor(max_workers=1)

discovery_future = None
pending_tasks = {}
copy_signal_queue = queue.Queue(maxsize=200)
seen_tokens = set()
rejected_tokens_until = {}

pending_sniper_tokens = {}
pending_discovery_tokens = {}

last_scan_time = 0
last_statistics_time = 0

last_prices = {}


# ==========================================
# HELPERS
# ==========================================

def log_event(event, address=None, reason=None):

    message = "[TROJAN V2] " + str(event)

    if address:
        message += " | TOKEN: " + str(address)

    if reason:
        message += " | REASON: " + str(reason)

    print(message, flush=True)


def safe_float(value):

    if isinstance(value, bool):
        return None

    try:
        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except (ValueError, TypeError, OverflowError):
        return None


def get_token_address(token):

    if not isinstance(token, dict):
        return None

    address = (
        token.get("tokenAddress")
        or token.get("address")
        or token.get("token_address")
        or token.get("mint")
    )

    if not isinstance(address, str):
        return None

    address = address.strip()

    return address if address else None



def merge_candidate_metadata(market_data, candidate):
    """Preserve discovery metadata when market providers omit token metadata."""
    merged = dict(market_data) if isinstance(market_data, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    candidate_meta = candidate.get("tokenMeta") if isinstance(candidate.get("tokenMeta"), dict) else {}

    def useful(value):
        return (
            isinstance(value, str)
            and value.strip()
            and value.strip().lower() not in {"unknown", "n/a", "none", "null"}
        )

    base_token = merged.get("baseToken") if isinstance(merged.get("baseToken"), dict) else {}
    for key in ("name", "symbol"):
        incoming = candidate.get(key)
        current = merged.get(key) if useful(merged.get(key)) else base_token.get(key)
        if useful(incoming) and not useful(current):
            merged[key] = incoming.strip()

    image = next((
        value for value in (
            candidate.get("icon"), candidate.get("logoURI"),
            candidate.get("logo_uri"), candidate.get("imageUrl"),
            candidate_meta.get("image"),
        ) if useful(value) and value.strip().startswith(("https://", "http://"))
    ), None)
    info = merged.get("info") if isinstance(merged.get("info"), dict) else {}
    if image and not useful(info.get("imageUrl")) and not useful(merged.get("logoURI")):
        merged["logoURI"] = image

    market_website_list = merged.get("websites")
    if not isinstance(market_website_list, list):
        market_website_list = info.get("websites")
    market_website = merged.get("website") or merged.get("website_url")
    has_market_website = useful(market_website) or (
        isinstance(market_website_list, list)
        and any(
            isinstance(item, dict) and useful(item.get("url"))
            for item in market_website_list
        )
    )

    candidate_websites = candidate.get("websites")
    if isinstance(candidate_websites, list) and candidate_websites:
        existing_info = dict(info)
        existing_websites = merged.get("websites")
        if not isinstance(existing_websites, list) or not existing_websites:
            existing_websites = existing_info.get("websites")
        if not isinstance(existing_websites, list) or not existing_websites:
            existing_info["websites"] = candidate_websites
            merged["info"] = existing_info
            merged["websites"] = candidate_websites
    direct_website = candidate.get("website") or candidate.get("website_url")
    if useful(direct_website) and not has_market_website:
        merged["website"] = direct_website.strip()

    for key in (
        "mintAuthority", "mint_authority", "freezeAuthority", "freeze_authority",
        "updateAuthority", "update_authority", "creator", "creatorAddress",
        "creator_address", "mutable", "isMutable", "is_mutable",
    ):
        if candidate.get(key) is not None and merged.get(key) is None:
            merged[key] = candidate[key]
    if candidate_meta.get("updateAuthority") is not None and merged.get("updateAuthority") is None:
        merged["updateAuthority"] = candidate_meta["updateAuthority"]

    return merged


def get_liquidity(pair):

    if not isinstance(pair, dict):
        return None

    liquidity_data = pair.get("liquidity")

    if not isinstance(liquidity_data, dict):
        return None

    liquidity = safe_float(liquidity_data.get("usd"))

    if liquidity is None or liquidity < 0:
        return None

    return liquidity


def remove_pending_duplicates(address):

    pending_sniper_tokens.pop(address, None)
    pending_discovery_tokens.pop(address, None)


# ==========================================
# RETRY HANDLER
# ==========================================

def queue_retry(
    address,
    strategy_name,
    token,
    first_seen,
    attempts,
    delay,
    reason,
    retry_on_wait=False
):

    current_time = time.time()

    maximum_age = (
        SNIPER_TOKEN_TIMEOUT
        if strategy_name == "sniper"
        else DISCOVERY_TOKEN_TIMEOUT
    )

    token_age = current_time - first_seen

    # Missing market data is common immediately after a new launch.
    # Keep checking transient WAIT results until the token's own
    # discovery window expires instead of dropping it after two tries.
    if retry_on_wait:

        if token_age >= maximum_age:

            log_event(
                "TOKEN WAIT WINDOW EXPIRED",
                address,
                reason
            )

            seen_tokens.discard(address)
            remove_pending_duplicates(address)

            return

    elif attempts >= MAX_ANALYSIS_RETRIES:

        log_event(
            "ANALYSIS RETRIES EXHAUSTED",
            address,
            reason
        )

        seen_tokens.discard(address)
        remove_pending_duplicates(address)

        return

    item = {
        "first_seen": first_seen,
        "token": token,
        "attempts": attempts + 1,
        "retry_after": current_time + delay
    }

    if strategy_name == "sniper":

        pending_discovery_tokens.pop(address, None)
        pending_sniper_tokens[address] = item

    else:

        pending_sniper_tokens.pop(address, None)
        pending_discovery_tokens[address] = item

    seen_tokens.discard(address)

    log_event(
        "ANALYSIS RETRY SCHEDULED",
        address,
        str(reason) + " | ATTEMPT: " + str(attempts + 1)
    )


# ==========================================
# RUGCHECK
# ==========================================

_RUGCHECK_REPORT_UNSET = object()


def check_rugcheck(token_address, report=_RUGCHECK_REPORT_UNSET):
    """Validate a RugCheck report, optionally reusing the already-fetched report."""

    try:

        result = (
            get_rugcheck_report(token_address)
            if report is _RUGCHECK_REPORT_UNSET else report
        )

        if not isinstance(result, dict):

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "EMPTY OR INVALID RESPONSE"
            )

            return False

        analysis = result.get("analysis")

        if not isinstance(analysis, dict):

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "ANALYSIS MISSING"
            )

            return False

        status = analysis.get("status")
        score = safe_float(analysis.get("score"))
        high_risks = safe_float(analysis.get("high_risks"))

        if status != "PASS":

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "STATUS: " + str(status) + " | "
                + "; ".join(str(item) for item in (analysis.get("reasons") or [])[:5])
            )

            return False

        if analysis.get("safe") is not True:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "TOKEN NOT SAFE"
            )

            return False

        if score is None or score >= MAX_RUGCHECK_SCORE:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "INVALID OR HIGH SCORE: " + str(score)
            )

            return False

        if high_risks is None or high_risks < 0:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "INVALID HIGH RISK COUNT"
            )

            return False

        if not high_risks.is_integer() or high_risks > 0:

            log_event(
                "RUGCHECK FAILED",
                token_address,
                "HIGH RISKS: " + str(high_risks)
            )

            return False

        return True

    except Exception as error:

        log_event(
            "RUGCHECK EXCEPTION",
            token_address,
            repr(error)
        )

        return False


def evaluate_pre_strategy_security(token_address):
    """Run mandatory risk checks before observation or strategy scoring.

    RugCheck always runs. On-chain mint/account checks run when RPC is configured.
    Set TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY=true to fail closed without RPC.
    """
    try:
        rugcheck_report = get_rugcheck_report(token_address)
    except Exception as error:
        log_event("RUGCHECK FETCH FAILED", token_address, repr(error))
        return {
            "status": "REJECT",
            "reason": "RUGCHECK_UNAVAILABLE",
            "rugcheck": None,
            "token_security": None,
        }

    rugcheck_analysis = (
        rugcheck_report.get("analysis")
        if isinstance(rugcheck_report, dict)
        and isinstance(rugcheck_report.get("analysis"), dict)
        else None
    )
    if not check_rugcheck(token_address, rugcheck_report):
        return {
            "status": "REJECT",
            "reason": "RUGCHECK_FAILED",
            "rugcheck": rugcheck_analysis,
            "token_security": None,
        }

    token_security_report = {
        "status": "SKIPPED",
        "reason": "SOLANA_RPC_NOT_CONFIGURED",
    }
    try:
        rpc_configured = bool(get_onchain_status().get("rpc_configured"))
    except Exception as error:
        log_event("ONCHAIN STATUS CHECK FAILED", token_address, repr(error))
        return {
            "status": "REJECT",
            "reason": "ONCHAIN_TOKEN_SECURITY_UNAVAILABLE",
            "rugcheck": rugcheck_analysis,
            "token_security": None,
        }

    require_onchain = os.getenv(
        "TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY", "false"
    ).strip().lower() in ("1", "true", "yes", "on")

    if not rpc_configured:
        if require_onchain:
            return {
                "status": "REJECT",
                "reason": "ONCHAIN_TOKEN_SECURITY_REQUIRED_BUT_RPC_MISSING",
                "rugcheck": rugcheck_analysis,
                "token_security": token_security_report,
            }
        log_event(
            "ONCHAIN TOKEN SECURITY SKIPPED",
            token_address,
            "RPC NOT CONFIGURED; set TROJAN_REQUIRE_ONCHAIN_TOKEN_SECURITY=true to fail closed",
        )
    else:
        try:
            from token_security import (
                get_token_security_report,
                evaluate_token_security,
            )
            import blacklist

            token_security_report = get_token_security_report(token_address)
            account_matches = blacklist.match_token_security_accounts(
                token_security_report
            )
            if account_matches:
                match = account_matches[0]
                log_event(
                    "BLACKLIST REJECT",
                    token_address,
                    "BLACKLISTED_ACCOUNT: " + match["reason"],
                )
                return {
                    "status": "REJECT",
                    "reason": "BLACKLISTED_ACCOUNT",
                    "blacklist": {
                        "entry_type": match["entry_type"],
                        "entry_value": match["entry_value"],
                        "reason": match["reason"],
                        "source": match["source"],
                    },
                    "rugcheck": rugcheck_analysis,
                    "token_security": token_security_report,
                }

            decision = evaluate_token_security(token_security_report)
            if not decision.get("allowed"):
                return {
                    "status": "REJECT",
                    "reason": decision.get(
                        "reason", "ONCHAIN_TOKEN_SECURITY_REJECTED"
                    ),
                    "rugcheck": rugcheck_analysis,
                    "token_security": token_security_report,
                }
        except Exception as error:
            log_event("ONCHAIN TOKEN SECURITY FAILED", token_address, repr(error))
            return {
                "status": "REJECT",
                "reason": "ONCHAIN_TOKEN_SECURITY_UNAVAILABLE",
                "rugcheck": rugcheck_analysis,
                "token_security": token_security_report,
            }

    return {
        "status": "PASS",
        "reason": "PRE_STRATEGY_SECURITY_PASSED",
        "rugcheck": rugcheck_analysis,
        "token_security": token_security_report,
    }


# ==========================================
# TOKEN ANALYSIS
# ==========================================

def analyze_token_task(address, strategy_name, token):

    import trading_control
    if trading_control.is_force_stopped():
        return {
            "status": "waiting",
            "reason": "FORCE STOP ACTIVE",
            "address": address,
        }

    token = token if isinstance(token, dict) else {}
    pair = token.get("market_data")
    if isinstance(pair, dict):
        pair_base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        pair_base_address = pair_base.get("address")
        if pair_base_address and pair_base_address != address:
            log_event("MARKET PAIR MINT MISMATCH", address, str(pair_base_address))
            pair = None
    if not isinstance(pair, dict):
        pair = get_market_data(address)

    # Provider resilience: when DexScreener has no complete quote, use the
    # exact pool discovered for this token as a GeckoTerminal fallback.
    # The fallback is accepted only when Gecko identifies this mint as the
    # pool's BASE token; we never transfer a quote-token price to this mint.
    discovered_pool = (
        token.get("poolAddress") or token.get("pool_address")
        or (pair.get("pairAddress") if isinstance(pair, dict) else None)
    )
    pair_price = safe_float(pair.get("priceUsd")) if isinstance(pair, dict) else None
    pair_liquidity = get_liquidity(pair) if isinstance(pair, dict) else None
    if (pair_price is None or pair_price <= 0 or pair_liquidity is None or pair_liquidity <= 0) and discovered_pool:
        try:
            gecko_fallback = get_gecko_pool_data_batch([discovered_pool])
            fallback_pair = gecko_fallback.get(address) if isinstance(gecko_fallback, dict) else None
            if isinstance(fallback_pair, dict):
                merged_pair = dict(pair) if isinstance(pair, dict) else {}
                merged_pair.update(fallback_pair)
                pair = merged_pair
                log_event("GECKO MARKET FALLBACK USED", address, discovered_pool)
        except Exception as fallback_error:
            log_event("GECKO MARKET FALLBACK FAILED", address, repr(fallback_error))

    if not isinstance(pair, dict) or not pair:

        return {
            "status": "waiting",
            "reason": "MARKET_DATA_MISSING",
            "address": address
        }

    try:

        pool_address = (
            token.get("poolAddress") or pair.get("pairAddress")
            or token.get("pool_address")
        )
        # Persistent denylist parity: block token mints and selected pools
        # before any market, strategy, or paper-entry work is performed.
        try:
            import blacklist
            token_meta = token.get("tokenMeta") if isinstance(token.get("tokenMeta"), dict) else {}
            authority_values = (
                token.get("mintAuthority"), token.get("mint_authority"),
                token.get("freezeAuthority"), token.get("freeze_authority"),
                token.get("updateAuthority"), token.get("update_authority"),
                token.get("creator"), token.get("creatorAddress"),
                token.get("creator_address"),
                token_meta.get("updateAuthority"),
            )
            blacklist_queries = [
                ("token", address),
                ("pool", pool_address),
            ]
            blacklist_queries.extend(
                ("account", value)
                for value in authority_values
                if isinstance(value, str) and value.strip()
            )
            blacklist_matches = blacklist.match_entries(blacklist_queries)
        except Exception as blacklist_error:
            log_event("BLACKLIST LOOKUP FAILED", address, repr(blacklist_error))
            return {
                "status": "rejected",
                "reason": "BLACKLIST_LOOKUP_UNAVAILABLE",
                "address": address,
            }
        if blacklist_matches:
            match = blacklist_matches[0]
            reason = {
                "token": "BLACKLISTED_TOKEN",
                "pool": "BLACKLISTED_POOL",
                "account": "BLACKLISTED_ACCOUNT",
            }.get(match["entry_type"], "BLACKLISTED_ENTRY")
            log_event("BLACKLIST REJECT", address, reason + ": " + match["reason"])
            return {
                "status": "rejected",
                "reason": reason,
                "blacklist": {
                    "entry_type": match["entry_type"],
                    "entry_value": match["entry_value"],
                    "reason": match["reason"],
                    "source": match["source"],
                },
                "address": address,
            }
        pool_protocol = (
            pair.get("dexId") or token.get("dexId")
            or token.get("poolName") or token.get("pool_name") or ""
        )
        if str(pool_protocol).lower() == "geckoterminal":
            pool_protocol = (
                token.get("dexId") or token.get("poolName")
                or token.get("pool_name") or ""
            )
        onchain_snapshot = None
        onchain_error = None
        rpc_configured = get_onchain_status().get("rpc_configured")
        if pool_address and rpc_configured:
            import pool_analysis
            pool_state = pool_analysis.get_pool_analysis_state(pool_address)
            if pool_state["state"] == "structural_quarantine":
                return {
                    "status": "rejected",
                    "reason": "POOL_STRUCTURE_INVALID",
                    "address": address,
                }
            if pool_state["state"] == "ready":
                try:
                    onchain_snapshot = decode_pool_snapshot(
                        pool_address, pool_protocol, address
                    )
                    import protocol_validation
                    protocol_validation.record_validation(
                        pool_protocol, pool_address, address,
                        snapshot=onchain_snapshot
                    )
                except Exception as error:
                    onchain_error = error
                    from solana_onchain import record_onchain_error
                    record_onchain_error(error)
                    import protocol_validation
                    protocol_validation.record_validation(
                        pool_protocol, pool_address, address, error=error
                    )
                    log_event("ONCHAIN POOL READ FAILED", address, repr(error))
                    if pool_analysis.classify_pool_error(error) == "structural":
                        return {
                            "status": "rejected",
                            "reason": "POOL_STRUCTURE_INVALID",
                            "address": address,
                        }
        if isinstance(onchain_snapshot, dict):
            pair = dict(pair)
            pair.update(onchain_snapshot)

        price = safe_float(pair.get("priceUsd"))
        liquidity = get_liquidity(pair)

        if price is None or price <= 0:

            return {
                "status": "rejected",
                "reason": "INVALID_PRICE",
                "address": address
            }

        if liquidity is None:

            return {
                "status": "waiting",
                "reason": "LIQUIDITY_MISSING",
                "address": address
            }

        if strategy_name == "sniper":

            if liquidity < SNIPER_MIN_LIQUIDITY:

                return {
                    "status": "waiting",
                    "reason": "SNIPER_LIQUIDITY_TOO_LOW",
                    "address": address
                }

        # GeckoTerminal OHLCV is denominated in the pool's base token.
        # Do not apply those indicators to a candidate that is the quote mint.
        base_token = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        indicators = (
            fetch_pool_indicators(pool_address)
            if pool_address and base_token.get("address") == address
            else {
                "ready": False,
                "reason": (
                    "Pool address unavailable" if not pool_address
                    else "OHLCV base token differs from candidate mint"
                )
            }
        )

        token_data = merge_candidate_metadata(pair, token)
        token_data["address"] = address
        token_data["indicators"] = indicators
        token_data["onchain_verified"] = bool(
            isinstance(onchain_snapshot, dict)
            and onchain_snapshot.get("onchainVerified") is True
        )

        sources = token.get(
            "sources",
            token.get("discovery_sources", [strategy_name])
        )

        if not isinstance(sources, list):
            sources = [strategy_name]

        token_data["discovery_sources"] = [
            source.strip().lower()
            for source in sources
            if isinstance(source, str) and source.strip()
        ]

        if not token_data["discovery_sources"]:
            token_data["discovery_sources"] = [strategy_name]

        if not isinstance(token_data.get("liquidity"), dict):
            token_data["liquidity"] = {"usd": liquidity}

        # Pool count comes only from the actual multi-source pool registry.
        # An empty or stale registry is left unknown rather than represented as zero.
        try:
            from pool_registry import get_pool_candidates
            registered_pools = get_pool_candidates(address)
            if registered_pools:
                token_data["pool_count"] = len(registered_pools)
        except Exception as pool_metric_error:
            log_event("POOL METRIC LOOKUP FAILED", address, repr(pool_metric_error))

        # Ordered, auditable filtering before strategy evaluation.
        # Optional activity/momentum thresholds are environment-configurable;
        # absent optional provider data is not silently converted to zero.
        import filter_pipeline
        filter_report = filter_pipeline.evaluate_token(
            token_data, address, strategy=strategy_name
        )
        if filter_report.get("status") == "WAIT":
            return {
                "status": "waiting",
                "reason": filter_report.get("reason", "FILTER_DATA_WAIT"),
                "filter_report": filter_report,
                "address": address,
            }
        if filter_report.get("status") != "PASS":
            return {
                "status": "rejected",
                "reason": filter_report.get("reason", "FILTER_REJECTED"),
                "filter_report": filter_report,
                "address": address,
            }

        # Security checks precede observation and strategy scoring. A candidate
        # cannot receive a BUY decision until the independent risk gates pass.
        security_result = evaluate_pre_strategy_security(address)
        if security_result.get("status") != "PASS":
            return {
                "status": "rejected",
                "reason": security_result.get("reason", "PRE_STRATEGY_SECURITY_REJECTED"),
                "address": address,
                "rugcheck": security_result.get("rugcheck"),
                "token_security": security_result.get("token_security"),
                "blacklist": security_result.get("blacklist"),
            }
        rugcheck_analysis = security_result.get("rugcheck")
        token_security_report = security_result.get("token_security")

        # Optional ScreenerBot-style live trade observation. PumpPortal trade
        # subscriptions are metered, so this stage is disabled unless the user
        # explicitly enables it and configures a PumpPortal API key.
        import candidate_observer
        observation_report = candidate_observer.observe_candidate(
            address, token_data.get("discovery_sources", [])
        )
        if not observation_report.get("ok"):
            return {
                "status": "waiting",
                "reason": observation_report.get("reason", "OBSERVATION_WAIT"),
                "observation": observation_report,
                "filter_report": filter_report,
                "address": address,
            }
        token_data["candidate_observation"] = observation_report

        account = paper.get_account_summary()

        open_positions = len(paper.get_positions())

        trade_decision = trading_strategy.evaluate_trade(
            token_data,
            account["balance"],
            open_positions,
            account["daily_loss"],
            strategy=strategy_name
        )

        if not isinstance(trade_decision, dict):

            return {
                "status": "rejected",
                "reason": "INVALID_STRATEGY_RESULT",
                "address": address
            }

        action = trade_decision.get("action")

        if action == "WAIT":
            return {
                "status": "waiting",
                "reason": trade_decision.get("reason", "WAITING_FOR_ENTRY"),
                "score": trade_decision.get("score", 0),
                "address": address
            }

        if action != "BUY_SIGNAL":
            return {
                "status": "rejected",
                "reason": trade_decision.get("reason", "STRATEGY_REJECTED"),
                "score": trade_decision.get("score", 0),
                "address": address
            }

        score = safe_float(trade_decision.get("score"))

        if score is None or not 0 <= score <= 100:

            return {
                "status": "rejected",
                "reason": "INVALID_APPROVED_SCORE",
                "address": address,
                "rugcheck": rugcheck_analysis
            }

        requested_size = (
            safe_float(token.get("copy_trade_size_cap"))
            or safe_float(trade_decision.get("trade_size"))
            or safe_float(getattr(paper, "TRADE_SIZE", 5.0))
        )
        try:
            from entry_intelligence import analyze_entry_route
            entry_intelligence = analyze_entry_route(address, requested_size)
            if not entry_intelligence.get("allowed"):
                return {
                    "status": "rejected",
                    "reason": entry_intelligence.get("reason", "ENTRY_ROUTE_REJECTED"),
                    "address": address,
                    "route_quality": entry_intelligence.get("route_quality"),
                    "simulation": entry_intelligence.get("simulation"),
                    "rugcheck": rugcheck_analysis
                }
        except Exception as route_error:
            log_event("JUPITER ENTRY ANALYSIS FAILED", address, repr(route_error))
            return {
                "status": "rejected",
                "reason": "JUPITER_ENTRY_ANALYSIS_UNAVAILABLE",
                "address": address,
                "rugcheck": rugcheck_analysis
            }

        return {
            "status": "approved",
            "address": address,
            "price": price,
            "liquidity": liquidity,
            "score": score,
            "strategy": strategy_name,
            "analysis": trade_decision,
            "filter_report": filter_report,
            "pool_address": pool_address,
            "market_source": pair.get("marketSource") or pair.get("dexId"),
            "pool_protocol": pool_protocol,
            "onchain_verified": bool(onchain_snapshot),
            "token_security": token_security_report,
            "rugcheck": rugcheck_analysis,
            "entry_intelligence": entry_intelligence,
            "candidate_observation": observation_report,
            "copy_wallet": token.get("copy_wallet") if isinstance(token, dict) else None,
            "copy_signature": token.get("copy_signature") if isinstance(token, dict) else None,
            "copy_latency_seconds": token.get("copy_latency_seconds") if isinstance(token, dict) else None
        }

    except Exception as error:

        log_event(
            "ANALYSIS EXCEPTION",
            address,
            repr(error)
        )

        return {
            "status": "error",
            "reason": "ANALYSIS_EXCEPTION",
            "address": address
        }


# ==========================================
# SCHEDULER
# ==========================================

def schedule_token(
    address,
    strategy_name,
    token=None,
    first_seen=None,
    attempts=0
):

    if not isinstance(address, str) or not address:
        return False

    if strategy_name not in ("main", "sniper"):
        return False

    if address in seen_tokens or address in pending_tasks:
        return False

    rejected_until = rejected_tokens_until.get(address, 0)

    if time.time() < rejected_until:
        return False

    if rejected_until:
        rejected_tokens_until.pop(address, None)

    if len(pending_tasks) >= MAX_PENDING_TASKS:

        log_event("TASK LIMIT REACHED", address)

        return False

    if address in paper.get_positions():
        return False

    if not isinstance(token, dict):
        token = {}

    seen_tokens.add(address)

    try:

        future = executor.submit(
            analyze_token_task,
            address,
            strategy_name,
            token
        )

        pending_tasks[address] = {
            "future": future,
            "strategy": strategy_name,
            "first_seen": (
                first_seen if first_seen is not None else time.time()
            ),
            "token": token,
            "attempts": attempts
        }

        log_event(
            "TOKEN QUEUED",
            address,
            strategy_name
        )

        return True

    except Exception as error:

        seen_tokens.discard(address)

        log_event(
            "TASK SUBMISSION ERROR",
            address,
            repr(error)
        )

        return False


# ==========================================
# COPY WALLET PAPER SIGNAL QUEUE
# ==========================================

def queue_copy_signal(event):
    if not isinstance(event, dict) or event.get("action") not in ("BUY", "SELL"):
        return False
    if not isinstance(event.get("token_mint"), str) or not event["token_mint"]:
        return False
    try:
        queued = dict(event)
        queued["queued_at"] = time.time()
        copy_signal_queue.put_nowait(queued)
        return True
    except queue.Full:
        log_event("COPY SIGNAL QUEUE FULL", event.get("token_mint"))
        return False


def process_copy_signals():
    processed = 0
    while processed < 25:
        try:
            event = copy_signal_queue.get_nowait()
        except queue.Empty:
            break
        processed += 1
        address = event.get("token_mint")
        wallet = event.get("wallet")
        action = event.get("action")

        # Reject delayed/replayed wallet activity before either paper BUY or SELL.
        from copy_freshness import evaluate_event_freshness
        freshness = evaluate_event_freshness(
            event.get("block_time"), event.get("received_at"),
            now=time.time()
        )
        if not freshness.get("allowed"):
            log_event(
                "COPY SIGNAL SKIPPED",
                address,
                str(freshness.get("reason")) + " | age=" + str(freshness.get("age_seconds"))
            )
            continue
        event["copy_latency_seconds"] = freshness.get("age_seconds")

        # Mirror a followed wallet's SELL only against the paper position
        # opened from that same wallet's BUY signal. Never close unrelated
        # manual or strategy positions just because a watched wallet sold.
        if action == "SELL":
            position = paper.get_position(address)
            metadata = position.get("metadata", {}) if isinstance(position, dict) else {}
            if not isinstance(metadata, dict) or metadata.get("copy_wallet") != wallet:
                log_event("COPY SELL SKIPPED", address, "no matching wallet-owned paper position")
                continue
            try:
                pair = get_market_data(address)
                price = safe_float(pair.get("priceUsd")) if isinstance(pair, dict) else None
                if price is None or price <= 0:
                    log_event("COPY SELL WAITING", address, "current price unavailable")
                    continue
                result = paper.close_position(address, price, "COPY WALLET MIRROR SELL")
                log_event("COPY SELL MIRRORED", address, str(result))
            except Exception as error:
                log_event("COPY SELL ERROR", address, repr(error))
            continue

        from copy_risk import check_copy_buy
        wallet_stats = next(
            (item for item in database.get_copy_wallet_performance()
             if item.get("wallet") == wallet),
            {}
        )
        decision = check_copy_buy(
            paper.get_positions(), wallet, address,
            daily_loss=wallet_stats.get("daily_loss", 0.0)
        )
        if not decision.get("allowed"):
            log_event(
                "COPY BUY RISK REJECTED",
                address,
                "wallet=" + str(wallet) + " | reason=" + str(decision.get("reason"))
            )
            continue
        token = {
            "tokenAddress": address,
            "chainId": "solana",
            "discovery_sources": ["copy_wallet"],
            "copy_wallet": wallet,
            "copy_signature": event.get("signature"),
            "copy_trade_size_cap": decision.get("trade_size"),
            "copy_latency_seconds": event.get("copy_latency_seconds")
        }
        queued = schedule_token(address, "sniper", token)
        log_event(
            "COPY BUY SIGNAL QUEUED" if queued else "COPY BUY SIGNAL SKIPPED",
            address,
            "wallet=" + str(wallet)
        )


# ==========================================
# COMPLETED TASKS
# ==========================================

def process_completed_tasks():

    for address, task in list(pending_tasks.items()):

        future = task["future"]

        if not future.done():
            continue

        del pending_tasks[address]

        try:
            result = future.result()

        except Exception as error:

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                ERROR_RETRY_INTERVAL,
                "FUTURE_EXCEPTION: " + repr(error)
            )

            continue

        if not isinstance(result, dict):

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                ERROR_RETRY_INTERVAL,
                "INVALID_RESULT"
            )

            continue

        status = result.get("status")
        reason = result.get("reason", "NO_REASON")

        if status == "waiting":

            database_decision = getattr(__import__("database"), "record_filter_decision")
            database_decision(
                address, "MARKET_AND_STRATEGY", "WAIT", reason,
                score=result.get("score"),
                liquidity=result.get("liquidity"),
                source_count=len(task["token"].get("discovery_sources", []))
                if isinstance(task.get("token", {}).get("discovery_sources"), list) else 0
            )

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                WAIT_RETRY_INTERVAL,
                reason,
                retry_on_wait=True
            )

            continue

        if status == "error":

            database.record_filter_decision(
                address, "ANALYSIS", "ERROR", reason
            )

            queue_retry(
                address,
                task["strategy"],
                task["token"],
                task["first_seen"],
                task["attempts"],
                ERROR_RETRY_INTERVAL,
                reason
            )

            continue

        if status != "approved":

            __import__("database").record_filter_decision(
                address, "MARKET_AND_STRATEGY", "REJECT", reason,
                score=result.get("score"), liquidity=result.get("liquidity")
            )

            remove_pending_duplicates(address)

            log_event(
                "TOKEN REJECTED",
                address,
                reason
            )

            # Rejected candidates may become viable later as liquidity,
            # volume and buyer flow change. Revisit after a short cooldown
            # instead of blacklisting them for the entire process lifetime.
            seen_tokens.discard(address)
            rejected_tokens_until[address] = (
                time.time() + REJECTED_TOKEN_RESCAN_INTERVAL
            )

            continue

        score = safe_float(result.get("score"))
        price = safe_float(result.get("price"))
        strategy_name = result.get("strategy")
        analysis = result.get("analysis")

        if (
            score is None
            or price is None
            or price <= 0
            or strategy_name not in ("main", "sniper")
            or not isinstance(analysis, dict)
        ):

            log_event(
                "INVALID APPROVED RESULT",
                address
            )

            remove_pending_duplicates(address)

            continue

        __import__("database").record_filter_decision(
            address, "COMPLETE_PIPELINE", "PASS",
            "Approved for paper entry evaluation",
            score=score, liquidity=result.get("liquidity"),
            source_count=len(task["token"].get("discovery_sources", []))
            if isinstance(task.get("token", {}).get("discovery_sources"), list) else 0
        )

        try:

            save_analysis(
                address,
                score,
                analysis.get("action", "BUY_SIGNAL"),
                mode="paper"
            )

        except Exception as error:

            log_event(
                "ANALYSIS SAVE ERROR",
                address,
                repr(error)
            )

        try:

            approved_trade_size = safe_float(analysis.get("trade_size"))
            copy_wallet = result.get("copy_wallet")
            if copy_wallet:
                from copy_risk import cap_copy_trade_size
                wallet_stats = next(
                    (item for item in database.get_copy_wallet_performance()
                     if item.get("wallet") == copy_wallet),
                    {}
                )
                approved_trade_size, copy_risk_decision = cap_copy_trade_size(
                    paper.get_positions(), copy_wallet, address, approved_trade_size,
                    daily_loss=wallet_stats.get("daily_loss", 0.0)
                )
                if approved_trade_size <= 0:
                    log_event(
                        "COPY BUY RISK REJECTED AT EXECUTION",
                        address,
                        "wallet=" + str(copy_wallet) + " | reason=" + str(copy_risk_decision.get("reason"))
                    )
                    continue
            trade_result = manager.process_approved_signal(
                address,
                price,
                strategy=strategy_name,
                trade_size=approved_trade_size,
                metadata={
                    "pool_address": result.get("pool_address"),
                    "market_source": result.get("market_source"),
                    "pool_protocol": result.get("pool_protocol"),
                    "onchain_verified": result.get("onchain_verified"),
                    "token_security": result.get("token_security"),
                    "entry_intelligence": result.get("entry_intelligence"),
                    "candidate_observation": result.get("candidate_observation"),
                    "copy_wallet": result.get("copy_wallet"),
                    "copy_signature": result.get("copy_signature"),
                    "copy_latency_seconds": result.get("copy_latency_seconds")
                }
            )

            log_event(
                "APPROVED SIGNAL",
                address,
                str(trade_result)
            )
            if isinstance(trade_result, dict) and str(trade_result.get("paper", "")).startswith("PAPER BUY:"):
                try:
                    from telegram_alerts import send_alert
                    send_alert(
                        "TROJAN PAPER BUY\nToken: " + address
                        + "\nStrategy: " + strategy_name
                        + "\nPrice: $" + str(price)
                        + "\nSize: $" + str(approved_trade_size if approved_trade_size is not None else paper.TRADE_SIZE)
                        + "\nLive trading: LOCKED"
                    )
                except Exception as alert_error:
                    log_event("TELEGRAM ALERT ERROR", address, repr(alert_error))

        except Exception as error:

            log_event(
                "TRADE MANAGER ERROR",
                address,
                repr(error)
            )

        remove_pending_duplicates(address)


# ==========================================
# DISCOVERY
# ==========================================

def add_discovery_tokens():

    tokens = []

    try:
        result = scan_tokens()
        if isinstance(result, (list, tuple)):
            tokens.extend(result)
    except Exception as error:
        log_event("SCANNER ERROR", reason=repr(error))

    try:
        result = collect_tokens()
        if isinstance(result, (list, tuple)):
            tokens.extend(result)
    except Exception as error:
        log_event("AGGREGATOR ERROR", reason=repr(error))

    # Merge all feeds before scheduling so a DexScreener candidate does not
    # hide useful GeckoTerminal pool metadata for the same mint.
    merged = {}
    invalid = 0
    for token in tokens:
        if not isinstance(token, dict):
            invalid += 1
            continue
        address = get_token_address(token)
        if not address:
            invalid += 1
            continue
        candidate = merged.setdefault(address, {
            "tokenAddress": address,
            "chainId": "solana",
            "discovery_sources": []
        })
        for key, value in token.items():
            if key == "discovery_sources":
                sources = value if isinstance(value, list) else [value]
                for source in sources:
                    if isinstance(source, str) and source.strip():
                        normalized = source.strip().lower()
                        if normalized not in candidate["discovery_sources"]:
                            candidate["discovery_sources"].append(normalized)
            elif key == "market_data" and isinstance(value, dict):
                # Prefer a concrete pool snapshot over absent metadata.
                candidate[key] = value
            elif key not in candidate or candidate[key] in (None, "", {}, []):
                candidate[key] = value

    # Fill market-data gaps in bounded batches before ranking. Tokens without
    # enough data remain in the watch/weak tiers; absence is not a safety verdict.
    candidates = list(merged.values())
    candidates.sort(
        key=lambda item: len(item.get("discovery_sources", []))
        if isinstance(item.get("discovery_sources"), list) else 0,
        reverse=True
    )
    missing_market = [
        item["tokenAddress"] for item in candidates
        if not isinstance(item.get("market_data"), dict)
    ][:90]
    market_enrichment = {}
    if missing_market:
        try:
            market_enrichment = get_market_data_batch(missing_market) or {}
        except Exception as error:
            log_event("DISCOVERY MARKET ENRICHMENT ERROR", reason=repr(error))

    candidates = rank_candidates(candidates, market_enrichment)
    quality_status = get_quality_status(candidates)

    added = 0
    already_seen = 0
    already_pending = 0
    already_open = 0
    cooldown = 0
    blocked = 0
    hard_rejected = 0

    for token in candidates:
        address = token["tokenAddress"]
        quality = token.get("candidate_quality", {})
        if quality.get("hard_reject") is True:
            hard_rejected += 1
            log_event(
                "DISCOVERY CANDIDATE REJECTED",
                reason=" | ".join(quality.get("reasons", []))[:240],
                token=address
            )
            continue
        if address in pending_tasks:
            already_pending += 1
            continue
        if address in seen_tokens:
            already_seen += 1
            continue
        if address in paper.get_positions():
            already_open += 1
            continue
        if time.time() < rejected_tokens_until.get(address, 0):
            cooldown += 1
            continue
        if schedule_token(address, "main", token):
            added += 1
        else:
            blocked += 1

    log_event(
        "DISCOVERY TOKENS QUEUED",
        reason=(
            "queued=" + str(added)
            + " | scanned=" + str(len(tokens))
            + " | unique=" + str(len(merged))
            + " | invalid=" + str(invalid)
            + " | already_seen=" + str(already_seen)
            + " | already_pending=" + str(already_pending)
            + " | open_positions=" + str(already_open)
            + " | reject_cooldown=" + str(cooldown)
            + " | blocked=" + str(blocked)
            + " | quality_strong=" + str(quality_status["tiers"]["strong"])
            + " | quality_watch=" + str(quality_status["tiers"]["watch"])
            + " | quality_weak=" + str(quality_status["tiers"]["weak"])
            + " | quality_hard_rejected=" + str(hard_rejected)
        )
    )


# ==========================================
# SNIPER QUEUE
# ==========================================

def receive_sniper_tokens():

    received = 0

    while True:

        try:
            token = sniper.token_queue.get_nowait()

        except queue.Empty:
            break

        except Exception as error:

            log_event("SNIPER QUEUE ERROR", reason=repr(error))
            break

        if not isinstance(token, dict):
            continue

        address = get_token_address(token)

        if not address:
            continue

        if address in seen_tokens or address in pending_tasks:
            continue

        pending_discovery_tokens.pop(address, None)

        pending_sniper_tokens[address] = {
            "token": token,
            "first_seen": time.time(),
            "attempts": 0,
            "retry_after": 0
        }

        received += 1

    if received:
        log_event("SNIPER TOKENS RECEIVED", reason=str(received))


def process_sniper_tokens():

    current_time = time.time()

    for address, item in list(pending_sniper_tokens.items()):

        if current_time - item["first_seen"] > SNIPER_TOKEN_TIMEOUT:

            log_event("SNIPER TOKEN EXPIRED", address)

            del pending_sniper_tokens[address]
            seen_tokens.add(address)

            continue

        if current_time < item.get("retry_after", 0):
            continue

        if address in pending_tasks:
            continue

        if address in paper.get_positions():

            del pending_sniper_tokens[address]
            continue

        schedule_token(
            address,
            "sniper",
            item.get("token", {}),
            first_seen=item["first_seen"],
            attempts=item.get("attempts", 0)
        )


# ==========================================
# DISCOVERY RETRIES
# ==========================================

def process_discovery_pending():

    current_time = time.time()

    for address, item in list(pending_discovery_tokens.items()):

        if current_time - item["first_seen"] > DISCOVERY_TOKEN_TIMEOUT:

            log_event("DISCOVERY TOKEN EXPIRED", address)

            del pending_discovery_tokens[address]
            seen_tokens.add(address)

            continue

        if current_time < item.get("retry_after", 0):
            continue

        if address in pending_tasks or address in pending_sniper_tokens:
            continue

        if address in paper.get_positions():

            del pending_discovery_tokens[address]
            continue

        schedule_token(
            address,
            "main",
            item.get("token", {}),
            first_seen=item["first_seen"],
            attempts=item.get("attempts", 0)
        )


# ==========================================
# SALE REPORTING
# ==========================================

def record_sale(address, result):

    if not isinstance(result, dict):
        return

    try:
        from telegram_alerts import send_alert
        send_alert(
            "TROJAN PAPER SELL\nToken: " + address
            + "\nReason: " + str(result.get("result", "SELL"))
            + "\nRealized P/L: $" + str(result.get("profit_loss", "n/a"))
        )
    except Exception as alert_error:
        log_event("TELEGRAM ALERT ERROR", address, repr(alert_error))

    log_event(
        "PAPER SALE",
        address,
        "P/L: "
        + str(result.get("profit_loss", "N/A"))
        + " | BALANCE: "
        + str(result.get("balance", "N/A"))
    )


# ==========================================
# POSITION MONITOR
# ==========================================

def monitor_positions():

    import service_health
    service_health.heartbeat("position_monitor")
    positions = list(paper.get_positions().items())
    from onchain_stream import sync_watched_pools
    watched_pools = []
    for address, position in positions:
        metadata = position.get("metadata", {}) if isinstance(position, dict) else {}
        if isinstance(metadata, dict):
            pool_address = metadata.get("pool_address")
            protocol = metadata.get("pool_protocol")
            if pool_address and protocol:
                watched_pools.append((pool_address, protocol, address))
    sync_watched_pools(watched_pools)

    if not positions:
        return

    addresses = [
        address
        for address, position in positions
        if isinstance(address, str) and address
    ]

    try:

        market_data = get_market_data_batch(addresses)

        if not isinstance(market_data, dict):
            log_event("POSITION BATCH ERROR", reason="INVALID RESPONSE")
            return

        missing_pool_addresses = [
            position.get("metadata", {}).get("pool_address")
            for address, position in positions
            if address not in market_data
            and isinstance(position, dict)
            and isinstance(position.get("metadata"), dict)
            and position["metadata"].get("pool_address")
        ]
        if missing_pool_addresses:
            gecko_fallback = get_gecko_pool_data_batch(missing_pool_addresses)
            market_data.update(gecko_fallback)

        # Prefer a fresh, owner-verified pool-account price when the stored
        # pool protocol is supported and an RPC endpoint is configured.
        if get_onchain_status().get("rpc_configured"):
            for address, position in positions:
                metadata = position.get("metadata", {}) if isinstance(position, dict) else {}
                pool_address = metadata.get("pool_address") if isinstance(metadata, dict) else None
                protocol = metadata.get("pool_protocol") if isinstance(metadata, dict) else None
                if not pool_address or not protocol:
                    continue
                try:
                    from onchain_stream import get_latest_snapshot
                    snapshot = get_latest_snapshot(
                        pool_address, protocol, address, max_age=1.0
                    )
                    if snapshot is None:
                        snapshot = decode_pool_snapshot(pool_address, protocol, address)
                    if snapshot:
                        existing = market_data.get(address, {})
                        merged = dict(existing) if isinstance(existing, dict) else {}
                        merged.update(snapshot)
                        market_data[address] = merged
                except Exception as error:
                    from solana_onchain import record_onchain_error
                    record_onchain_error(error)
                    log_event("ONCHAIN POSITION PRICE UNAVAILABLE", address, repr(error))

    except Exception as error:

        log_event(
            "POSITION BATCH ERROR",
            reason=repr(error)
        )

        return

    for address, position in positions:

        pair = market_data.get(address)

        if not pair:
            continue

        price = safe_float(pair.get("priceUsd"))

        if price is None or price <= 0:
            continue

        liquidity = get_liquidity(pair)

        last_prices[address] = price

        try:

            liquidity_status = check_liquidity(
                address,
                liquidity
            )

            if liquidity_status == "COLLAPSE":

                log_event(
                    "EMERGENCY LIQUIDITY EXIT",
                    address
                )

                result = paper.emergency_sell(
                    address,
                    price
                )

                record_sale(address, result)
                continue

            if liquidity_status in ("UNKNOWN", "WARNING"):

                log_event(
                    "LIQUIDITY STATUS",
                    address,
                    liquidity_status
                )

        except Exception as error:

            log_event(
                "LIQUIDITY MONITOR ERROR",
                address,
                repr(error)
            )

        try:

            result = paper.update_position(
                address,
                price
            )

            record_sale(address, result)

        except Exception as error:

            log_event(
                "POSITION UPDATE ERROR",
                address,
                repr(error)
            )


# ==========================================
# STATISTICS
# ==========================================

def print_statistics():

    try:

        stats = get_trade_stats("paper")
        account = paper.get_account_summary()

        print("\n========== TROJAN V2 ==========")

        print("PAPER BALANCE:", account["balance"])
        print("PAPER POSITIONS:", account["positions"])
        print("PAPER INVESTED:", account["invested"])

        print("MAIN POSITIONS:", paper.position_count("main"))
        print("SNIPER POSITIONS:", paper.position_count("sniper"))

        print("PENDING ANALYSES:", len(pending_tasks))
        print("DISCOVERY PENDING:", len(pending_discovery_tokens))
        print("SNIPER PENDING:", len(pending_sniper_tokens))

        print("CLOSED TRADES:", stats["total_trades"])
        print("WIN RATE:", stats["win_rate"])
        print("REALIZED P/L:", stats["total_profit"])

        print("LIVE MODE: SHADOW ONLY")
        print("LIVE EXECUTION: LOCKED")

        print("===============================\n")

    except Exception as error:

        log_event("STATISTICS ERROR", reason=repr(error))


# ==========================================
# MAIN LOOP
# ==========================================

def run_bot():

    global last_scan_time
    global last_statistics_time
    global discovery_future

    print("====================================")
    print("TROJAN V2 - SOLANA AI TRADING BOT")
    print("====================================")
    print("PAPER TRADING: ENABLED")
    print("WORKERS:", WORKERS)
    print("MAX PENDING TASKS:", MAX_PENDING_TASKS)
    print("MARKET DATA: BATCH ENABLED")
    print("LIVE EXECUTION: LOCKED")
    print("====================================")

    initialize_database()
    paper.refresh_account()

    try:
        from dashboard import start_dashboard
        start_dashboard()
    except Exception as dashboard_error:
        log_event("DASHBOARD START ERROR", reason=repr(dashboard_error))

    sniper.start_listener()

    try:
        from copy_trade import start_copy_monitor
        start_copy_monitor()
    except Exception as copy_error:
        log_event("COPY WATCH START ERROR", reason=repr(copy_error))

    log_event("SNIPER THREAD STARTED")
    log_event("BOT STARTED")

    while True:

        loop_start = time.time()

        try:

            # Free completed task slots first.

            process_completed_tasks()
            process_copy_signals()

            # Receive and schedule new tokens.

            receive_sniper_tokens()
            process_sniper_tokens()
            process_discovery_pending()

            # Run network-heavy discovery separately so API latency cannot
            # block the position monitor and stop-loss checks.
            if discovery_future is not None and discovery_future.done():

                try:
                    discovery_future.result()
                except Exception as error:
                    log_event("BACKGROUND DISCOVERY ERROR", reason=repr(error))

                discovery_future = None

            if (
                discovery_future is None
                and time.time() - last_scan_time >= SCAN_INTERVAL
            ):

                discovery_future = discovery_executor.submit(
                    add_discovery_tokens
                )

                last_scan_time = time.time()
                log_event("BACKGROUND DISCOVERY STARTED")

            # Monitor all positions through batch market data.

            monitor_positions()
            import service_health
            service_health.heartbeat("main_loop")

            if time.time() - last_statistics_time >= STATISTICS_INTERVAL:

                print_statistics()
                last_statistics_time = time.time()

        except Exception as error:

            log_event("MAIN LOOP ERROR", reason=repr(error))

        elapsed = time.time() - loop_start

        time.sleep(
            max(1, POSITION_CHECK_INTERVAL - elapsed)
        )


# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":

    try:

        run_bot()

    except KeyboardInterrupt:

        log_event("BOT SHUTTING DOWN")

        executor.shutdown(
            wait=False,
            cancel_futures=True
        )

        discovery_executor.shutdown(
            wait=False,
            cancel_futures=True
        )
