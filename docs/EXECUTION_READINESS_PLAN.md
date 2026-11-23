# Trojan V2 execution-readiness plan

This document is the acceptance ledger for the five stabilization milestones.
A commit or passing unit suite is not, by itself, proof of trading performance.

## 1. Stabilization

**Implemented in this pass**
- Adaptive paper sizing now ignores non-finite P/L rows and reports the number ignored.
- Learning is isolated by MAIN/SNIPER strategy and uses completed paper SELL rows only.
- Three consecutive losses cap the multiplier at 0.90x; five cap it at 0.80x.
- Adaptive sizing is rechecked against the configured maximum ticket, remaining-balance fraction, minimum ticket, daily loss and position limits immediately before the paper database write.
- Native transaction broadcast has a code-level hard lock. Setting `TROJAN_NATIVE_EXECUTE_ENABLED=true` cannot unlock it.
- Dashboard adaptive panel shows expectancy, current/max losing streak, realized P/L drawdown and invalid-row count.

**Acceptance evidence**
- Unit tests cover adaptive sample gating, bounded sizing, loss-streak response, strategy isolation, invalid P/L and final risk-cap enforcement.
- Native execution tests verify the environment variable cannot bypass the hard lock.
- GitHub Actions full suite must pass on the branch head.

**Still required before calling stabilization complete**
- A clean deployment health/readiness snapshot after restart.
- A verified database backup/restore drill on the deployed instance.
- A review of live provider error/latency telemetry during a sustained run.

## 2. Test runs

**Automated checks**
- GitHub Actions runs `python -m unittest discover -s tests -v` on feature pushes and pull requests.
- Tests are deterministic unit/integration tests with mocked external RPC/provider calls where appropriate.

**Operational test evidence still required**
- At least 24 hours of uninterrupted paper operation.
- Record trade count, net realized P/L after modeled fees/slippage, win rate, profit factor, maximum account-equity drawdown, API failure rate, quote rejection rate and position-monitor heartbeat gaps.
- Keep MAIN and SNIPER results separate.
- Do not infer profitability from unit tests or a short sample.

## 3. Adaptive Learning V2

**Implemented**
- Per-strategy paper-only learning profile.
- Minimum sample gate (default 30 completed SELL outcomes).
- Win rate, profit factor, expectancy, average win/loss, current/max losing streak and realized P/L peak-to-trough drawdown.
- Bounded size multiplier from 0.80x to 1.15x; loss-streak and negative-expectancy brakes.
- No entry-rule mutation, no live-mode data and no live-execution authority.

**Not implemented / deliberately not claimed**
- No ML model, no market-regime classifier and no adaptive entry/exit signal generation.
- A profitable edge has not been demonstrated. Any signal-learning extension requires out-of-sample validation and a frozen baseline.

## 4. Dashboard

**Implemented**
- Authenticated dashboard with paper portfolio, open positions, strategy editor, readiness, discovery/provider health, protocol validation, database health/backup controls and adaptive-learning panel.
- Adaptive panel now exposes the new risk/quality metrics.

**Acceptance evidence still required**
- Confirm the deployed dashboard reflects the current branch commit.
- Verify mobile layout and refresh behaviour against a running deployment.
- Verify all protected APIs reject unauthenticated requests and no private key/seed material is requested or stored.

## 5. Live Trading readiness

**Current state: NOT READY; hard locked.**
- Native broadcast hard lock is enabled in code. Environment configuration alone cannot unlock it.
- External-wallet-only signing design is retained; no server private-key custody or signing.
- Native builders support a restricted subset of Raydium CPMM, PumpSwap V2, Orca Whirlpool and Meteora DLMM. This is not proof that all pools/tokens are executable.
- Token-2022 execution support and full Meteora DLMM bin-array traversal remain incomplete.
- No end-to-end externally signed devnet transaction has been proven in this project session.
- No mainnet transaction, live profitability, or ScreenerBot outperformance is claimed.

**Required before any future unlock**
1. Complete devnet end-to-end transaction preparation, external wallet signing, signed simulation, submission and confirmation tracking with test funds only.
2. Add Token-2022 and protocol edge-case coverage or reject unsupported assets explicitly.
3. Resolve multi-replica approval storage (durable shared store or enforced sticky routing).
4. Complete independent security review and failure/replay/expiry tests.
5. Produce at least 24-hour paper evidence and a controlled same-market comparison against the ScreenerBot baseline.
6. Obtain explicit user approval for a separately reviewed release. The hard lock must not be removed as part of ordinary feature work.

## Anti-loop rule

Every follow-up task must state: (a) the defect or missing capability, (b) the exact files changed, (c) the acceptance test/evidence, and (d) the remaining blocker. Do not repeat a previously completed implementation unless a reproducible failing test or new protocol requirement justifies it.
