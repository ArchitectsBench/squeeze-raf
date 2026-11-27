# Squeeze + RAF → Options-Spread Recommendation Engine

A two-layer trading research system:

1. **Signal layer** — a directional edge on the *underlying* (SPX-500 + high-vol ETFs) on
   2h / 4h / 1d bars, from the confluence of two indicators (TTM-Squeeze-Pro variant + a
   Ready-Aim-Fire / RAF momentum filter). Target: **OOS Sharpe ≥ 1.2 trailing 2y.**
2. **Structuring layer** — converts a graduated signal into a **debit or credit vertical
   spread**: structure from the IV regime, strikes from delta + expected move, expiration from
   the holding-horizon bucket (1 / 2 / 3 / 5 days).

The two layers are validated **separately**. The Sharpe target lives on the signal layer; the
spread layer is proven later on real historical options data.

This is a research tool. Trading in it is paper only, and nothing here is financial advice.

## Read these first
- `CLAUDE.md` — the operating rules given to the coding agent that helped build this (the same text is in `AGENTS.md`).
- `docs/BUILD_SPEC.md` — the full spec: indicator math, strategy logic, validation pipeline,
  matrices, data plan, architecture.
- `docs/PHASES.md` — copy-paste prompts to drive the build, P1 → P9.
- `decisions.md` / `risks.md` — living registers.
- `config/gates.yaml` / `config/universe.yaml` — pre-registered thresholds and universe/costs.

## Setup
Requires Python 3.12 (`pyproject.toml` pins `>=3.12,<3.13`) and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest          # look-ahead + fidelity tests must be green before trusting any result
```

## The prime directive
Validate the entry rule's raw edge BEFORE optimizing. Optimize only with walk-forward. Graduate
only after the full overfitting gauntlet (Deflated Sharpe + PBO + bootstrap + permutation). Lock
the holdout. Never confuse the underlying's Sharpe with the spread's. Build gates, not just
scripts.

## What is in the repository

| Path | What it holds |
|---|---|
| `indicators/`, `signals/` | The two indicator ports and the confluence signal |
| `edge_check/`, `backtest/`, `optimize/`, `validation/` | Edge check, backtest, walk-forward optimisation and the overfitting tests |
| `spreads/`, `options_backtest/` | The spread recommender and its options backtest |
| `data/` | Vendor adapters, caching and resampling (code only, no data) |
| `reporting/`, `webapp/` | Screener, trade plans, notifications and a web UI |
| `tests/` | 122 tests, including look-ahead and indicator-fidelity tests |
| `config/` | Pre-registered gate thresholds and the universe |
| `decisions.md`, `risks.md` | Dated decision log and risk register |

In total: 131 Python files and about 13,900 lines of Python.

## What this copy is

This is a public export of a private working repository, taken at one point in time.

- **Commit history is not included.** This copy starts from a single export commit; later commits are changes made to the export itself.
- **No market data is included.** The adapters need your own vendor keys.
- **One internal audit document is left out** because it contains account-level results.
- **Machine paths** in scripts and the `launchd` job definitions are replaced with `/Users/you`.
- **The full test suite has not been run against this export.** All 122 tests are collected.
