# Spec — Historical OI → GEX backtest

**Goal:** add open-interest (OI) to the cached ORATS history, compute *historical* GEX with the
same math as the live engine (`reporting/gex.py`), and backtest whether the dealer-gamma
regime actually improves squeeze-setup selection — turning the one unvalidated layer in the
system into a tested (or rejected) signal.

## Prerequisite (BLOCKER)
The ORATS S3 key is currently **invalid** (`InvalidAccessKeyId`, confirmed 2026-06-30 — it was
live 06-29, so rotated/expired). A fresh `ORATS_AWS_ACCESS_KEY_ID` / `ORATS_AWS_SECRET_ACCESS_KEY`
in `.env` is required before any re-pull. Code changes (Phase 1) don't need creds; only the
S3 re-pull does.

## Why OI isn't already cached
`options_backtest/orats_adapter.py` `_KEEP` whitelists columns and **drops OI**; the 290 cached
parquets (2025-01..2026-06) were built without it, and `load_day()` returns the cache if present
(never re-pulls). So: keep the field AND invalidate the stale cache.

## Phase 0 — confirm field names (1 S3 call, after creds)
Pull one raw day, list columns. Expected OI fields: **`cOpInt` / `pOpInt`** (fallbacks `cOi`/`pOi`
or `callOpenInterest`/`putOpenInterest`). Grab volume (`cVolu`/`pVolu`) too as a liquidity filter.
Gamma note: ORATS gives one `gamma` per strike row — correct for BOTH call & put (BS gamma is
identical at a strike/expiry/vol), so use the row gamma against both `cOpInt` and `pOpInt`.

## Phase 1 — keep OI + invalidate stale cache  (CODE-ONLY, no creds)
`options_backtest/orats_adapter.py`:
- Add `cOpInt, pOpInt, cVolu, pVolu` to `_KEEP` (the column names are matched leniently — any
  that are absent in the raw file are skipped, so unknown exact spelling won't crash; Phase 0
  confirms and we adjust the list if ORATS uses different spellings).
- `load_day()` schema guard: if a cached parquet lacks an OI column, re-fetch from S3 and
  overwrite (idempotent; avoids a blanket cache wipe; old no-OI days self-heal on next read).
- Re-pull window: `OratsAdapter(universe).prefetch(dates)` for 2025-01..2026-06 (~290 days,
  ~15-30 min, few hundred MB, gitignored).

## Phase 2 — historical GEX (mirror the live engine)
New `options_backtest/gex_hist.py` → `gex_at(adapter, ticker, date, n_expiries=3)`:
- per strike: call term `gamma·cOpInt`, put term `gamma·pOpInt`.
- `net_gex = (Σcall − Σput)·100·spot²·0.01`; `regime_sign`; `call_wall = argmax(gamma·cOpInt)`;
  `put_wall = argmax(gamma·pOpInt)`; `flip` via the same BS-grid zero-cross as `reporting/gex.py`
  (have `cMidIv` + `yte` per strike).
- Deliberately identical math to `reporting/gex.py` so we validate the LIVE signal, not a proxy.

## Phase 3 — backtest
New `validation/gex_squeeze_backtest.py` — reuse squeeze-setup detection from
`validation/vehicle_backtest.py` over the ORATS window.
- at each setup date: `gex_at(...)` → bucket by NEG vs POS gamma + wall proximity.
- forward outcome: dir-adjusted stock return over 15d hold (as in vehicle_backtest).
- PRE-REGISTERED hypotheses (set thresholds BEFORE running, per gate discipline):
  - H1 (core): NEG-gamma squeeze setups beat POS on win-rate/fwd return; gate NEG−POS ≥ Δ,
    bootstrap p < 0.05.
  - H2: longs with room to the call wall beat longs pinned under it.
  - H3: is the verdict's ±8 GEX nudge justified out-of-sample?
- report win-rate + mean fwd return per bucket WITH n + significance test.

## Phase 4 — wire back honestly
- H1 confirms → keep/tune the ±8, document effect size.
- H1 fails → REMOVE the GEX conviction adjustment, keep walls as levels only.
- update `docs/market_internals.md` + memory.

## Honest caveat
~17-month ORATS window → ~149 setups split NEG/POS = small per-bucket samples. This is a
DIRECTIONAL read, not graduation — pre-register thresholds, report n. A clean verdict needs the
longer history (Tier-1 data asset: long survivorship-free history + historical OI).
