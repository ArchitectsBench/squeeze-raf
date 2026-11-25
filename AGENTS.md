# Project: Confluence Squeeze + RAF → Options-Spread Recommendation Engine

## What this is
Prove a directional signal edge on the UNDERLYING (SPX-500 + high-vol ETFs) on 2h/4h/1d
bars — OOS Sharpe >= 1.2 trailing 2y — then overlay a debit/credit vertical-spread
recommender (structure from IV regime, strikes from delta + expected-move, DTE from holding
horizon) validated separately with historical options data.

## Where the detail lives (read on demand — do NOT paste these into here)
- `docs/BUILD_SPEC.md`  — indicator math, strategy logic, full pipeline, matrices, data plan,
  architecture, phase plan. **Read the relevant section before implementing a phase.**
- `decisions.md` / `risks.md` — living registers. Update them as you make choices.
- `config/gates.yaml` — pre-registered gate thresholds (do not edit to make a strategy pass).
- Per-phase prompts come from me in chat (or `docs/PHASES.md`).

## Non-negotiable rules (NEVER violate in any code path)
1. NO LOOK-AHEAD. Signal on close[t], execute at open[t+1]. The RAF displaced TEMA is a
   BACKWARD lag: `savg = tavg.shift(14)`. Never `shift(-14)`.
2. COSTS ALWAYS ON (commission + slippage). No frictionless runs in reported results.
3. A strategy MUST pass the Stage-1 edge-check (event study, pre-registered gate) BEFORE any
   optimization. Never optimize an unvalidated rule.
4. Every optimization MUST use walk-forward; report WFA efficiency.
5. Every graduate MUST pass ALL of: Deflated Sharpe (trial-count corrected) + PBO/CSCV < 0.2
   + bootstrap MC (5th-pct acceptable, median >= 1.2) + permutation test + parameter-
   neighborhood + subperiod stability.
6. Indicator ports MUST pass fidelity tests vs ToS fixtures before use.
7. Deterministic seeds everywhere; log each run's config hash.
8. Point-in-time universe (or log a survivorship caveat).
9. Underlying-signal Sharpe != spread Sharpe. Validate the spread layer separately.

## Anti-overfitting discipline (for YOU, the AI)
- The OOS / holdout window is LOCKED. Do not read, tune to, or report on it until the final
  graduation gate (Phase 6).
- Pre-register all gate thresholds in `config/gates.yaml` BEFORE running. Do not move goalposts.
- Prefer broad parameter plateaus over sharp peaks. Flag knife-edge optima as suspect.
- Keep >= ~30 trades per free parameter; refuse to optimize past that budget.

## Workflow
- Work in phases (P1–P9 in `docs/BUILD_SPEC.md` §10.3). One phase per branch; commit at each
  green gate.
- TEST-FIRST for look-ahead and indicator fidelity — these are where shortcuts manufacture
  fake Sharpe. Write the failing test first.
- End every phase by showing me the metrics table and STOPPING for my go/no-go. Do not chain
  phases unsupervised.

## Stack
Python 3.12 (uv). Research/backtest: `vectorbt`. Optimize: `optuna`. Overfit math: `pypbo`
(+ a direct Deflated-Sharpe impl). Data cache: `parquet` via `pyarrow`. Tests: `pytest`.

## Commands
- Install: `uv sync`
- Test:    `uv run pytest`        # look-ahead + fidelity tests must be green before any results
- Lint:    `uv run ruff check .`
