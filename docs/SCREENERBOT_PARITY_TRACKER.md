# ScreenerBot parity review

Trojan baseline: `41b33dc` (draft PR #116). ScreenerBot reference:
`9cc0b74f9136c9cc5a4fe6b806471268c9b5ce1f`.

[Reference review status](SCREENERBOT_REVIEW_STATUS.tsv) gives each tracked
ScreenerBot path a status. `reviewed` requires reading the complete file and
recording a functional comparison. Files listed only in the earlier
[functional inventory](screenerbot-file-inventory.tsv) retain their unreviewed
status in this review.

| Trojan file | ScreenerBot counterpart | Finding and remaining gaps | Change and commit | Validation |
| --- | --- | --- | --- | --- |
| `discovery/token_aggregator.py` | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs` | Both merge concurrent provider results and isolate provider errors. Trojan had no byte-level mint check or valid/invalid counts. Reference has per-feed configuration, rate-lane selection, chain-specific discovery, persistent upserts, blacklist/existing-record checks and cancellable futures. Trojan still hands candidates to downstream paper ranking and filtering. | `11c0a5a` Validate discovery candidates and publish consistent scan metrics. Validate public-key bytes, exclude quote assets, report counts and duration, and publish consistent status without exposing provider exception text. | Syntax and diff checks passed. Added mocked invalid input and status isolation coverage. Suite reserved for batch checkpoint. |
| `scanner.py` | `src/tokens/discovery_sources.rs`, `src/apis/dexscreener/{mod,endpoints,types}.rs` | Parallel endpoint requests and independent outcomes already existed. Two Trojan entrypoints named the same endpoints differently, inflating source agreement after merge. Reference uses typed responses and endpoint-specific rate budgets. The latter needs separate API client review. | `21eac6a` Normalize standalone DexScreener discovery provenance. Reuse canonical labels and mint validation, use monotonic cooldown and fill missing metadata from later results. Community takeovers remain enabled. | Syntax and diff checks passed. Added mocked coverage. |
| `discovery/dexscreener_scanner.py` | `src/tokens/discovery_sources.rs`, `src/apis/dexscreener/{endpoints,types}.rs` | Reference uses typed metadata and retains feed-level outcomes. Trojan preserves provider fields beyond reference candidate records, but its first-hit merge discarded later fields and omitted valid zero boosts. Separate lane and per-feed status remain for the shared client. | `183887e` Retain supplemental DexScreener discovery metadata. Merge missing fields deterministically, retain explicit zero and log exception classes. | Syntax and diff checks passed. Added mocked metadata coverage. |
| `discovery/gecko_scanner.py` | `src/tokens/discovery_sources.rs`, `src/apis/geckoterminal/{endpoints,types}.rs` | All three discovery endpoints match. Reference emits both pool sides and typed market data across more windows. Trojan attaches normalized base-token snapshots and previously discarded later pools even when the first had no price. Quote-side flow/price orientation requires market-data review before expansion. | `2e8e548` Preserve usable GeckoTerminal snapshots during discovery deduplication. Enforce pool resource identity when supplied and prefer a later valid snapshot over a metadata-only hit. | Syntax and diff checks passed. Added malformed pool and duplicate fixture. |
| `discovery/birdeye_scanner.py` | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs` (provider isolation and rate budgets) | No direct Birdeye discovery client identified at the pinned ScreenerBot ref. Trojan provides the optional V3 and trending feeds. Its quota response was unnecessarily retried against the trending endpoint before cooldown. A shared account-quota state across Birdeye scanners remains open. | `c4767f1` Honor Birdeye quota exhaustion before trying fallback. Keep compatibility fallback for non-quota errors and reject boolean volumes. | Syntax and diff checks passed. Added quota and no-second-request regression. |
| `discovery/pumpfun_scanner.py` | `src/tokens/discovery.rs`, `src/tokens/discovery_sources.rs` (provider coordination) | Trojan uses Birdeye for meme listings; no matching Birdeye feed identified in ScreenerBot. Disparate quota state caused follow-up requests to the same account after quota exhaustion. Shared cooldown cannot cancel a sibling request already in flight. | `f25280c` Share Birdeye quota cooldown across discovery endpoints. Reuse parsing and quota policy from the sibling scanner, preserving the listing-specific fallback and metadata. | Syntax and diff checks passed. Added cross-feed provider regression. |
| `discovery/screenerbot_sources.py` | `src/tokens/discovery_sources.rs`, `src/apis/{rugcheck,coingecko,defillama}/{mod,types}.rs`, `src/chains/solana/apis/jupiter/sources.rs` | Route names and Solana filtering overlap. Reference permits JSON null for empty RugCheck stats, uses recent metadata and retains decimals. Jupiter featured boards, native price fallback and shared interactive/background budgeting remain unverified here. | `e2365e3` Preserve discovery metadata and accept empty RugCheck stats. Distinguish transport failure, retain decimals and merge feed metadata, and validate decoded mint length. | Syntax and diff checks passed. Added empty/failure and metadata regression fixtures. |
| `sniper_scanner.py` | `src/chains/solana/pools/discovery.rs`, `src/tokens/discovery.rs` | No PumpPortal discovery subscription was found at the pinned reference. Its pool discovery validates public keys before handing descriptors to the analyzer and stops with the service; Trojan receives token launch events instead, with a bounded queue and reconnect loop. Listener shutdown and metrics remain gaps. | `a74ebe4` Validate PumpPortal candidates before reserving queue space. Reuse the REST discovery mint gate, preserve deduplication and retry on queue overflow, and omit untrusted metadata and exception text from logs. | Added mocked WebSocket callback coverage for invalid addresses, duplicates and full-queue redelivery. |
| `discovery/__init__.py` | Python package marker; no direct Rust counterpart | The file contains only a comment. It has no functions, imports, API calls or initialization behavior. | No code change required. | Static inspection. |

| `candidate_quality.py` | `src/tokens/priorities.rs`, `src/filtering/sources/market.rs` | Reference prioritizes refresh by lifecycle state and applies provider-specific filtering after resolving the market feed. Trojan scores discovery candidates before admission. State-based refresh remains for market worker review. | `0a5d132` Harden candidate ranking and isolate quality status. Treat invalid timestamps as unknown, rank tied scores by distinct source count, tolerate malformed enrichment keys, and isolate the published counters. | All 577 tests passed. |

| `token_security.py` | `src/chains/solana/assets/mint.rs` | Reference reads exact supply, decimals and authorities from one typed mint lookup. Trojan reads parsed RPC and also reports holder concentration. Holder snapshots remain separate reads. | `a889b66` Read exact supply from the SPL mint snapshot. Validate input length and parsed account kind, preserve raw supply and retain the lookup fallback for responses without embedded supply. | All 578 tests passed. |

| `rugcheck.py` | `src/tokens/security/rugcheck.rs` | Trojan uses a bounded in-memory cache and rejects unsafe/incomplete reports. Reference also persists reports and has optional shared caching. Persistent and shared caches remain open. | `d0490d8` Reconcile competing authority values conservatively and match creator public keys case-sensitively. Integrate normalized risk bands into report output. | 580 tests passed. |
| `rugcheck_risk_band.py` (new) | `src/tokens/security/risk_band.rs` | Explicit bands were missing. The independent Python module validates provider scores and supplies low, medium and high reporting labels. | `d0490d8` Integrated through the existing RugCheck analysis entry point. Existing admission thresholds are retained. | Boundary and invalid-input tests pass through the report entry point. |

| `filter_pipeline.py` | `src/filtering/stage.rs`, `src/filtering/sources/{market,bounds}.rs` | Reference short-circuits in stage order and distinguishes optional ranges from required floors. Trojan has separate WAIT outcomes, but evaluated every stage eagerly and could pass a missing age with a positive floor. | `94f38b6` Run only reached stages, compare market base mint to the requested mint, and require valid age evidence for positive age floors. | Regression tests pass. |
| `blacklist.py` | `src/trader/safety/blacklist.rs` | Both consult authoritative DB state; Trojan separates token, pool and account matches. Reference position-exit policy belongs to the exit review. | `04ca872` Bound base58 decoding and skip malformed rows while deduplicating typed checks. | Regression tests pass. |

## Checkpoint

The [Trojan review status](TROJAN_REVIEW_STATUS.tsv) enumerates every tracked
path in this branch, including review artifacts. Fifteen of 61 production
Python files have completed this review. The last completed file is
`blacklist.py`. Resume at `risk_manager.py` for position admission.

The ScreenerBot ledger contains 3,043 tracked paths. The source classification
counts 2,011 files by programming, script, markup, stylesheet and SQL
extensions, including source-form tests and tooling. All other tracked paths
are listed as well. Twenty-three source files are reviewed and 1,988 remain
unreviewed. The ledger gives the exact unreviewed paths.

Checkpoint validation used Python 3.12 and isolated SQLite storage.
All 584 tests passed, including the process-level paper/dashboard and live
execution lock checks and database backup/restore tests. This is checkpoint
validation. Full project parity and current external provider or Devnet
behavior are unverified by this checkpoint.
