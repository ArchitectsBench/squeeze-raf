# Phase Prompts — Claude Code

Run these **in order**, one per branch. Each ends at a gate: Claude Code shows you the metrics
table and STOPS for your go/no-go. Full context for each phase is in `docs/BUILD_SPEC.md`.
Paste the block as-is (adapt bracketed bits).

---

## Bootstrap (first message)

```
Read CLAUDE.md and docs/BUILD_SPEC.md §9 (repo skeleton) and §10. Scaffold the repo:
pyproject.toml is already present — run `uv sync`. Create the module tree from §9 as STUB
files: each module gets a docstring stating its contract, but NO logic yet. Create the tests/
folder. decisions.md, risks.md, config/gates.yaml, config/universe.yaml already exist — read
them, do not overwrite. Show me the tree and confirm `uv run pytest` collects (even if empty),
then STOP.
```

---

## P1 — Data layer
**Gate: clean 2y pilot data cached + integrity test green**
```
Implement the data layer. Polygon + Alpha Vantage adapters for 2h/4h/1d OHLCV. Parquet cache
under data/ (gitignored). Pull the pilot universe from config/universe.yaml. Handle splits.
Write a data-integrity test: no gaps, no duplicate timestamps, monotonic index, no NaN bars.
Show a coverage report (rows per symbol per timeframe, date range), then STOP.
```

## P2 — Indicators + fidelity
**Gate: fidelity tests green on all ToS fixtures**
```
Port squeezeultra and RAF to Python EXACTLY per docs/BUILD_SPEC.md §2 and the traps in §7 and
indicators/CLAUDE.md. Critical: RAF savg = tavg.shift(14), NEVER shift(-14). Keep the squeeze
BB(EMA basis)/KC(SMA basis, EMA-of-TrueRange width) asymmetry as-is. I will provide ToS export
fixtures. TEST-FIRST: write fidelity tests asserting numeric match within tolerance, then
implement until green. Show the per-series diff stats vs ToS, then STOP.
```

## P3 — Edge-check (BEFORE optimization)
**Gate: a rule passes the locked edge gate in config/gates.yaml**
```
Implement Confluence v0 (§3) with modes A/B/C and the strategy_defaults from
config/universe.yaml. Build the Stage-1 event study + the pre-registered edge gate from
config/gates.yaml. Run it on the pilot universe across 2h/4h/1d. Show the forward-return table,
hit rate, t-stats, monotonicity vs conviction, and the random-entry baseline — per mode, per
timeframe. Give a PASS/FAIL per the gate. Do NOT optimize anything. STOP.
```

## P4 — Backtest engine
**Gate: look-ahead test green; metrics plausible**
```
Build the vectorbt backtest engine: signal on close[t], execute at open[t+1], costs always on
(config/universe.yaml), vol-target sizing, long AND short. Run the passing Confluence mode on
the pilot universe across all signal timeframes. Output the full metrics table (CAGR, Sharpe,
Sortino, MaxDD, Calmar, exposure, trades, win rate, profit factor, turnover) + an equity/
drawdown tearsheet. Add a unit test that FAILS if any code path uses same-bar close to fill the
same bar. STOP.
```

## P5 — Optimization + walk-forward
**Gate: WFA-efficient, broad-plateau top configs**
```
Implement coarse grid → Optuna with anchored walk-forward per §5 Stage 3. Enforce the
min_trades_per_free_parameter budget from config/gates.yaml. The OOS window stays LOCKED — do
not read it. Output: top-20 configs, WFA efficiency (OOS/IS), and a parameter plateau heatmap.
Flag any knife-edge optima as suspect. STOP.
```

## P6 — Overfitting gauntlet
**Gate: ≥1 config clears the full graduation gate**
```
On the top 10–20 configs, run the full battery (§5 Stage 4): Deflated Sharpe (trial-count
corrected), PBO via CSCV (pypbo), block-bootstrap MC, permutation test, parameter-neighborhood
robustness, subperiod stability. Now you may evaluate on the locked OOS window. Produce ONE
ranked table with every metric and a PASS/FAIL against config/gates.yaml graduation thresholds.
STOP.
```

## P7 — Spread engine
**Gate: recommendations sane vs hand-checks**
```
Implement IV-regime (IV-rank/percentile + term structure), the debit/credit decision matrix
(§6), delta + expected-move strike selection, and DTE-from-holding-horizon (config/universe.yaml).
For current signals on the pilot universe, output spread recommendations: structure, both
strikes + deltas, net debit/credit, max profit, max loss, breakevens, POP, expectancy, IV-rank
context. STOP.
```

## P8 — Options backtest
**Gate: spread layer validated on real IV data**
```
Add the ORATS adapter (or ThetaData). Reconstruct entry/exit option P&L for graduated signals
from historical options data. Compute realized SPREAD Sharpe, POP, expectancy, max profit/loss.
Compare spread Sharpe vs the underlying-signal Sharpe and explain the delta. STOP.
```

## P9 — Recommender CLI
**Gate: recs reproduce on a fixed date**
```
Build reporting/recommend.py: multi-timeframe scan over the full universe, alignment bonus when
4h + 1d agree, ranked recommendation output (CSV + tearsheet) tagged by timeframe and holding
bucket. Add a --paper mode wiring to a Tradier sandbox for forward testing. Show recs for a
fixed historical date and confirm they reproduce deterministically. STOP.
```
