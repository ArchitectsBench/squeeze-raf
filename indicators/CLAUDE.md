# Indicator porting rules (read before touching squeeze.py / raf.py)

Full math: `docs/BUILD_SPEC.md` §2. These are the traps that silently break a port and
manufacture fake edge — get them right.

## Hard rules
- **T1 — RAF displaced TEMA is a BACKWARD lag.** ThinkScript `GetValue(tavg, 14)` == value 14
  bars ago → `savg = tavg.shift(14)`. **Never `shift(-14)`** (forward/centered) — that injects
  look-ahead. There is a unit test that must fail on a forward shift.
- **T2 — Squeeze basis asymmetry is intentional.** Bollinger uses an **EMA** basis; Keltner
  uses an **SMA** basis with **EMA-of-TrueRange** for width. Replicate exactly; do not "tidy."
- **T3 — TTM histogram = linear-regression form** over `Length` of
  `close − ½·(Donchian-mid(L) + SMA(close, L))`. Match ToS numerically.
- **T4 — `StochasticFull` uses EXPONENTIAL averaging** in RAF; `TEMA` and `ADX` must follow
  ToS conventions.
- **T7 — Leveraged/vol ETFs:** split-adjust; UVXY/VXX carry structural decay.

## Fidelity is a gate
No indicator is used downstream until its Python output matches the ThinkOrSwim export fixture
within tolerance (Stage 0 / Phase 2). Write the fidelity test FIRST, then implement.

## Squeeze state encoding (don't flip it)
`3 = tightest compression` (BB inside 1.0-ATR KC), `2 = mid`, `1 = loose`, `0 = no squeeze`.
A "fire" = state transitions from ≥1 to 0.
