# Decision Register

Living log of design decisions. Claude Code: update this whenever you make a choice that a
future reader would otherwise have to reverse-engineer. Format: keep the table current, append
to the log. Detail for each lives in `docs/BUILD_SPEC.md`.

## Status table

| #  | Decision | Choice | Status | Owner |
|----|----------|--------|--------|-------|
| D1 | Sharpe ≥ 1.2 target applies to… | The **underlying signal** strategy, OOS, trailing 2y (NOT the spread) | **Accepted** | Owner |
| D2 | Meaning of the 6 "timeframes" | 2h/4h/1d = signal bars; 1/2/3/5d = holding-horizon → DTE buckets | **Accepted** | Owner |
| D3 | Direction | Long **and** short (generalize the long-only squeeze "ideal" symmetrically) | **Accepted** | Owner |
| D4 | Debit vs credit driver | IV regime (IV-rank/percentile + term structure), not the price signal | **Accepted** | Owner |
| D5 | Overfit defense | Walk-forward + Deflated Sharpe + PBO/CSCV + bootstrap MC + permutation | **Accepted** | Owner |
| D6 | Universe realism | Point-in-time SPX-500 constituents (or logged survivorship caveat) + curated high-vol ETFs | **Open** — pick data source | Owner |
| D7 | Backtest engine | `vectorbt` (OSS) for research; upgrade to VectorBT PRO if robustness sweeps get slow; Nautilus only for live | Accepted | Owner |
| D8 | Underlying data | Alpha Vantage for the 15-symbol pilot; Polygon (or Databento) for the full universe | Accepted | Owner |
| D9 | Options data | Deferred to Phase 8. ORATS (depth to 2007) as default; EODHD as budget pilot | **Open** — buy at P8 | Owner |
| D10 | Position sizing | Volatility-target (fixed % risk at the ATR stop), not fixed share count | Accepted | Owner |
| D11 | Confluence mode | A / B / C tested head-to-head in Phase 3; data picks the winner | Open — resolved by P3 | — |

| D12 | 2h/4h bar construction | Fetch native **1h** RTH bars, resample session-anchored to the **09:30 ET** open (label-left, RTH only); daily fetched native | Accepted (P1) | Claude |
| D13 | Split adjustment scope | **Split-only** (no dividend adjustment) to keep the price signal on as-traded levels; AV daily back-applies the split coefficient, Polygon/AV-intraday use vendor `adjusted=true` | Accepted (P1) | Claude |
| D14 | Daily-bar timestamp | UTC-midnight of the ET trading date, identical across vendors | Accepted (P1) | Claude |

## Decision log
- **D12–D14 (P1, 2026-06-15):** data-layer construction choices. Vendors don't serve 2h/4h, so
  we build them from 1h bars anchored to the session open — this is what the P2 indicator-fidelity
  work (ToS parity) will key off. Split-only adjustment is the minimum required by T7; revisit if a
  signal proves dividend-sensitive. Coverage report + integrity test (no gaps/dupes/NaN, monotonic,
  OHLC-sane) are the green P1 gate.
- **D8 caveat (P1):** Alpha Vantage `TIME_SERIES_DAILY_ADJUSTED` and intraday extended history are
  **premium** endpoints. The AV adapter targets the pilot only; Polygon is the path for the full pull.
- **D8 — Polygon key is FREE TIER (2026-06-15):** confirmed by the pilot pull — **2 years** of history
  (data starts 2024-06-17), **5,000 bars/page**, and **5 req/min** (hit 429s at 100/min). Limiter now
  defaults to 5/min (`POLYGON_CALLS_PER_MIN` to override on a paid plan). 2h/4h share one memoized 30-min
  pull. Consequence: the "trailing 2y OOS Sharpe" gate is feasible on free-tier depth, but there is **no
  pre-2024 buffer** for indicator warmup or longer subperiod-stability windows — upgrade the Polygon plan
  (or use Databento, D6) before the full SPX-500 run / P5 robustness work.
- **D1–D6** set at project kickoff (see `docs/BUILD_SPEC.md` §1). The cheap layer (underlying
  signal) is gated first; options spend is deferred until a signal graduates.
- **D9 note:** do NOT buy options data until ≥1 signal clears the full graduation gate (P6).
  No point validating a spread layer on a signal that hasn't earned it.
- **OPEN items to close before the relevant phase:** D6 (before P1), D9 (before P8), D11 (by P3).

- **P2 (2026-06-15):** indicators ported (squeeze + RAF) with trap tests green (T1 backward shift,
  T2 BB/KC basis asymmetry, T3 linreg histogram, Fisher, SC staging). **ToS numeric fidelity gate is
  PENDING user-supplied exports** (none in repo). Harness `tests/test_indicator_fidelity.py` auto-runs
  on CSVs dropped in `tests/fixtures/tos/` (schema in that dir's README). A few ToS conventions are
  assumed (EMA seeding, StDev ddof=0, Wilder ATR, exponential stochastics) and will be locked by the
  first fixture that disagrees.
- **P3 EDGE-CHECK RESULT = FAIL (2026-06-15):** Confluence v0 with DEFAULT params does NOT clear the
  locked Stage-1 gate on the 2y free-tier pilot, any mode × any timeframe.
  - **Modes A & B are structurally near-empty (~0 entries):** the literal *same-bar AND* of a squeeze
    *fire* (breakout) with RAF *SC≥1 / full Fire* (exhaustion-inflection) almost never co-occurs — the
    two engines are designed to be opposite (spec §2.3). Not a bug; the conjunction is too tight.
  - **Mode C runs** (~240 entries on 1d) but no edge: t-stat 0.92 (<2.0), hit 0.55, fails beats-random.
  - Per CLAUDE.md rule 3, STOPPED — did not optimize. **Open question for Owner:** is "confluence"
    meant as same-bar AND (current literal reading) or **windowed** (squeeze fired within last K bars
    AND RAF staged within last K bars)? The latter is the likely intent and would give A/B real entry
    counts — but changing it is a rule-spec decision, not a tuning knob (must not goal-hack, R8).

- **P3 re-run with WINDOWED confluence (2026-06-15, Owner-approved spec clarification):** confluence
  read as co-occurrence within `confluence_window` (=energy_lookback=6) bars, not same-bar. A/B now
  produce entries. Result still **FAIL** — no (mode,tf) clears the locked gate. Point estimates are
  encouraging (Mode A 4h: hit 0.57, +46 bps net, beats random, monotonic; Mode B 1d: hit 0.71, +177
  bps, beats random) but **every combo fails on t-stat < 2.0**. Binding constraint = statistical
  power: entry counts are small (A 1d ≈95, B 1d =7) on the 2y free-tier window, and pooled t-stats
  even OVERSTATE significance (entries overlap in time and across correlated names — effective N <<
  nominal). Root cause ties to the **D8 free-tier 2y data cap** — more history → more independent
  entries → a real significance test. Did NOT tune further (R8). Awaiting Owner decision.

- **P3 re-run on 5y data (2026-06-15, Massive/Polygon Starter ~5y, META from 2022-06-15 post-FB→META):**
  deeper sample ~doubled t-stats but still **FAIL** at the pre-registered primary horizon (h=4 bars):
  Mode C 1d t=1.99 (hit 0.571, +59 bps, beats random, monotonic) — misses 2.0 by 0.01; Mode A 4h
  t=1.82 (hit 0.560, +37 bps, beats random, monotonic). Did NOT cherry-pick (Mode A 4h is t=2.71 at
  h=1, but h=4 was pre-registered) and did NOT shave the threshold (R8). Caveat: pooled t-stats
  OVERSTATE significance (overlapping + cross-name-correlated entries → effective N << nominal).
  Strengthened the integrity checker (intraday block-gap detection) after META's FB→META 132-day hole
  passed the per-session check; all 48 frames now clean. Next lever = more independent entries
  (broaden universe), not tuning.

- **P3 PASS on broad universe (2026-06-15):** broadened to 54 sector-diversified liquid names
  (config `universe.broad`, survivorship caveat logged). The tech-concentrated signals WASHED OUT
  under diversification (Mode C 1d t 1.99→0.60 — it was a tech-momentum artifact), but **Mode A 2h
  PASSES the locked gate cleanly**: n=4787, @h=4 hit 0.535, +30.8 bps net, **t=7.78**, beats random
  (~0 baseline), monotonic conviction — and robustly significant across h=1..16 (t 3.1/7.8/5.6/3.3).
  Mode A 4h echoes it (t≈1.8-2.3). Nothing tuned; broadening (Owner-chosen lever) surfaced it.
  → Graduate Mode A to P4. CAVEATS carried to P6: pooled t overstates significance (overlapping +
  correlated entries) and multiple-testing across runs — both corrected by DSR/PBO in the gauntlet.

- **P4 backtest (2026-06-15):** event engine — next-bar-open fills, costs always on, vol-target
  sizing, long+short, ATR-stop / R-target / time / momentum exits. Look-ahead test suite green
  (entry fills open[t+1], signal bar contributes 0; gap-through-stop risk documented). Mode A
  full-sample (in-sample, 5y broad): **2h Sharpe 1.25** (Sortino 1.87, Calmar 1.26, PF 1.27, MaxDD
  -0.6%, 4682 trades), 4h 0.21, 1d 0.42. CAGR tiny in absolute terms — equal-weight-of-54-mostly-flat
  portfolio is under-deployed; Sharpe is scale-invariant so the gate metric is unaffected (a
  portfolio-vol-target overlay is a later refinement). **Caveat: high turnover ~969/yr at 2h** — costs
  matter; watch fragility. Gate (look-ahead green + plausible) met → P5. This Sharpe is IN-SAMPLE; the
  OOS/locked-window test is P6.

- **P5 optimize + walk-forward = FAIL (2026-06-15):** Mode A optimized across 2h/4h/1d (coarse grid
  → Optuna, anchored WFA, final 6mo OOS LOCKED). Research Sharpes look fine (2h best ~1.82, broad
  plateau, no knife-edge, 2057 trades) BUT **WFA efficiency is poor/negative**: 2h=0.28, 4h=-0.12,
  1d=-0.19 (norm for "efficient" is ≥~0.5). Per-fold test Sharpes swing +2/-1.4 — train-best configs
  don't generalize to the next fold. 4h also fails the ≥150-trade budget (128). Interpretation: the
  Stage-1 *entry* edge was real, but the full STRATEGY (exits + vol-target sizing + ~969 trades/yr
  cost drag) gives it back and is regime-unstable. **Did NOT proceed to P6** — refused to spend the
  locked OOS on a candidate that fails walk-forward. Likely culprits: exit design + turnover/cost.
  Best configs saved to optimize/best_configs.json. Awaiting Owner decision on rule/exit mutation.

- **P5 round 2 — exit redesign = PASS for 2h (2026-06-15, Owner-approved):** root cause of the round-1
  WFA fail was the tight 1.5×ATR stop cutting winners as noise. Widened to a catastrophe-only stop
  (2.5–4×ATR) and compared exit styles (pure time-stop vs target/momentum). **2h WFA efficiency
  0.28→0.72** (all 4 test folds positive, mean test Sharpe 1.55), research Sharpe 1.88 on a very broad
  plateau (top vs next-9 gap 0.08), 2053 trades, no knife-edge. 4h/1d still fail WFA (−0.28/−0.20) —
  the edge is a 2h phenomenon (matches the Stage-1 result). → 2h graduates to P6; unlock the locked OOS.

- **P6 GRADUATION GAUNTLET = FAIL — Confluence v0 does NOT graduate (2026-06-15):** ran the full
  battery on the P5 2h graduate and unlocked the OOS for the first time. Scorecard:
  PASS — bootstrap Sharpe p5=1.19 (≥0.5) & median=1.88 (≥1.2); permutation 99.9pct (≥95, timing skill
  real); subperiod all years positive (2021 1.21 / 2022 2.50 / 2023 2.24 / 2024 0.53 / 2025 2.34);
  neighborhood robust (min neighbor 1.54). FAIL — **OOS Sharpe 1.13 (<1.2, just misses)**, **PBO/CSCV
  0.54 (<0.20 needed)**, **DSR 0.0 (<0.95)**. Interpretation: the entries carry a REAL but WEAK edge
  (permutation/subperiod/bootstrap positive), but it is NOT strong/stable enough to survive correction
  for the multi-config SEARCH — PBO says "best-in-research" generalizes ~no better than a coin flip,
  and after deflating for ~120 trials the Sharpe is not significant. This is the gauntlet doing its
  job (R3/R8). Per CLAUDE.md rule 5 (must pass ALL), Confluence v0 does not graduate → do NOT proceed
  to P7-P9 (spread/options layers are only for a graduated signal). Pipeline ran end-to-end P1-P6.

- **Confluence v1 (Mode A + 4h alignment) — 8/9 graduation, fails only PBO (2026-06-15):** the
  conviction-threshold filter was DROPPED (v0 conviction score too discrete — clusters at 0.35;
  ≥0.40 retains 7% → nukes sample). 4h-regime alignment is the usable quality lever (keeps 61%,
  causal: 4h bar used only after it completes). Result vs v0: OOS Sharpe 1.13→**1.43** (PASS),
  WFA eff 0.72→**0.81**, DSR now **1.0** (PASS — also fixed a UNIT BUG: across-trial Sharpe variance
  must be per-bar not annualized when fed to DSR; affected v0 too). Bootstrap (p5 1.03/med 1.77),
  permutation (99.8), subperiod (all yrs +), neighborhood (1.37), budget (1251) all PASS. **Only PBO
  fails: 0.298 (need <0.20)** — a flat plateau of ~equal configs makes "best-IS" flip OOS. Caveats:
  PBO failure is the soft (plateau) kind; DSR=1.0 uses optimistic n_trials=12 (full-project search is
  larger). Per rule 5 (must pass ALL), v1 does NOT cleanly graduate. Awaiting Owner call on whether to
  proceed to P7 with the caveat or keep hardening.

- **P7 spread engine built (2026-06-15, Owner: proceed with v1 caveat):** §6 structuring layer —
  realized-vol IV PROXY + Black-Scholes (no options data yet; P8 swaps in real IV/greeks). Modules:
  iv_regime (rank/percentile/regime), structure (debit/credit matrix), strikes (delta+EM selection +
  full spread metrics), dte (holding bucket→DTE), recommend (assembler+CLI). 11 hand-check tests green
  (matrix, strike deltas, P/L=width, breakevens, POP). Gate "sane vs hand-checks" met. **Finding:**
  the 2h→0-2 DTE bucket gives DEBIT spreads cheaply (matches the squeeze-expansion thesis) but makes
  CREDIT spreads thin (credit/width ~0.10 vs 0.33-0.50 target at ~1 DTE) — for 2h signals the
  debit/low-IV branch is the strong setup; high-IV→credit at 1 DTE is weak. P8 (real data) to quantify.
- **REMAINING: P8 needs PAID options data** (ORATS/ThetaData/EODHD per D9) — external dependency +
  Owner decision; CLAUDE.md says don't buy until a signal graduates (v1 is borderline, PBO 0.30).
  P9 (multi-TF recommender CLI + Tradier paper mode) is buildable without new data.

- **P9 recommender CLI built (2026-06-15):** reporting/recommend.py — multi-TF scan (2h v1 + 4h/1d
  Mode A), 4h+1d alignment bonus (+0.20 aligned / +0.10 partial / -0.15 conflict; aligned signals
  bumped to the longer-horizon DTE bucket), ranked by adjusted conviction, CSV + tearsheet, Tradier
  `--paper` dry-run (gated on TRADIER_TOKEN/ACCOUNT), and `--as-of DATE` causal historical reproduction.
  **Gate MET: recs reproduce deterministically on a fixed date** (two `--as-of 2026-05-15` runs are
  byte-identical; as-of is causal — no signal_time > date). 110 tests green. NOTE: longer-DTE (1d→7-21)
  recs give more workable credit spreads than the 2h→0-2 DTE ones (the thin-credit finding). Pipeline
  now spans P1-P7 + P9; only P8 (real options P&L) remains, blocked on paid options data (D9).

- **P8 options backtest = spread layer does NOT add value (2026-06-15, real ORATS data):** built the
  ORATS S3 adapter (download/cache daily SMV-Strikes chains filtered to universe) + spread-P&L
  reconstructor (real bid/ask fills, real deltas) + runner. On 159 reconstructed spreads (6mo, true
  OOS-ish) for the v1 2h signal: **underlying ann Sharpe +1.57 → realized SPREAD Sharpe −3.77** (POP
  0.40, mean −15.3% on risk). Longer DTE (14-30) was WORSE (−8.90, low gamma → no payoff, pure bid/ask
  bleed). **Root cause: the 2h directional move over ~1.5 days is too small to move any vertical enough
  to clear options bid/ask+commission (~15-18% of risk/round-trip).** The signal is tradeable as STOCK
  (2bps, +1.57) but the options overlay erases the tiny per-trade edge at every DTE. This is exactly
  CLAUDE.md rule 9 (spread Sharpe != underlying Sharpe), decisively negative. P8 gate "validated on real
  IV data" MET (the validation says: don't use this spread overlay for this signal). EOD data can't
  price intraday round-trips (same-day trades excluded). **AWS key in .env should be ROTATED (pasted in
  chat).** Pipeline now complete P1-P9.

- **Higher-timeframe spread sweep (2026-06-15, real ORATS, 6mo window):** tested whether higher TFs
  give viable OPTIONS opportunities (bigger moves vs frictions). Spread Sharpe by TF: 2h (0-2 DTE)
  **−3.77**, 4h (2-5 DTE) **−6.87**, 1d (7-21 DTE) **+0.99** (underlying 2h +1.57 / 4h +0.08 / 1d +3.15).
  **The options overlay only works at 1d** — big enough moves + long enough DTE to clear bid/ask. BUT
  the 1d underlying +3.15 is IN-WINDOW and CONTRADICTS the P5 1d WFA fail (eff -0.20 over full history)
  — this 6mo window is likely favorable, NOT a durable edge. Conclusion: 1d is the right ZONE for the
  options product; next step is a 1d-level signal that is WFA-ROBUST (apply v1-style improvements to 1d
  + run the full P3-P6 funnel) before trusting the positive spread result.
- **1d spread robustness check (18mo) = the 6mo +0.99 did NOT hold:** over 18 months 1d underlying
  Sharpe +0.34 (weak, matches WFA fail), spread **−1.35**. The 6mo positive was a favorable-window
  artifact. CONCLUSION: 1d is mechanically the right ZONE for options (a positive spread IS achievable
  when the signal is right), but no current TF has a DURABLE higher-TF edge — 2h edge is real but too
  small for options; 1d moves suit options but the 1d signal lacks robust edge. Missing piece = a
  WFA-robust daily-swing signal. Don't trust short-window spread positives (R8 discipline).

- **Confluence v2 (daily swing + weekly alignment) = FAIL, no edge (2026-06-15):** built daily Mode A
  gated by weekly EMA-ribbon trend, swing exits, full funnel. **Stage-1 edge is NEGATIVE** (mean -40.8
  bps, t=-0.90 at h=4d) — per rule 3 it shouldn't even be optimized; the funnel rejected it. Permutation
  14.1 pctile (real Sharpe BELOW random timing), bootstrap p5 -0.64, DSR 0.18, subperiod unstable,
  neighborhood -0.36, WFA -1.03 — all FAIL. OOS 2.17 & PBO 0.07 "pass" are MISLEADING artifacts of a
  near-zero-edge signal (uniform mediocrity → low PBO; lucky 6mo holdout → high OOS). **Definitive
  finding: the Confluence squeeze+RAF edge is a 2h INTRADAY phenomenon; it does not exist at the daily
  swing horizon.** Combined with P8: the 2h edge is real but too small for options; the daily horizon
  suits options but has no edge. The options-spread thesis is NOT supported by this signal at any TF.
  Tradeable outcome = the borderline 2h signal AS STOCK; the options recommender needs a different,
  larger-move signal (open research, user's call).

- **Plain 1d Mode A — full gauntlet = FAIL, no edge (2026-06-15, definitive):** ran the complete P6
  battery on the plain daily signal. Only OOS Sharpe 1.26 "passes" (the same lucky recent holdout that
  drove the +3.15/+0.99 6mo spread result). Everything else fails: bootstrap median 0.40 / p5 -0.34
  (true Sharpe ~0.4, downside negative), permutation 55.9 pctile (≈ no timing skill), PBO 0.262, DSR
  0.346, subperiod 2/5 years negative, neighborhood -0.06. CONCLUSION (closes the 1d question): the 1d
  signal has NO durable edge; its apparent strength is favorable-window mirage that the unbiased tests
  (bootstrap/permutation/subperiod) reject. Final map: edge exists ONLY at 2h; 4h/1d/weekly-aligned-1d
  all have no edge. No daily signal rescues the options thesis.

- **Squeeze-ONLY exploration (no RAF) — real but modest 2h edge, none at 1d (2026-06-15):** decoupled
  the squeeze fire from RAF (pure expansion breakout). **2h: a REAL, robust edge** — Stage-1 t=3.14
  (n=11623, +7.7bps), **PBO 0.036** (vs combined v1's failing 0.30) and neighborhood robust → config
  choice is rock-solid; the squeeze fire IS the edge carrier and RAF was hurting robustness. BUT WEAKER
  magnitude (research Sharpe 0.75 vs v1 1.88, bootstrap median 0.74) → fails the graduation magnitude
  bars (OOS -1.57 bad-holdout, DSR 0.82, bootstrap median 0.74<1.2, permutation 74pct) + rough 2021
  (-2.48). **1d squeeze-only: NO edge** (Stage-1 t=-1.34, research 0.03, all fail). CONCLUSION: confirms
  — across combined AND squeeze-only — the edge lives ONLY at 2h intraday; it does not exist at 1d. The
  squeeze/RAF signal family is now thoroughly mapped: a modest 2h stock edge, never options-viable. A
  different signal CLASS would be needed for the daily/options goal.

- **New signal class — trend-following (daily), Stage-1 = FAIL via BETA confound (2026-06-15):** built
  a Donchian-breakout + SMA(200)-filter trend signal (a different class from squeeze/RAF). At h=20d it
  looks strong in isolation (t=3.99, hit 0.54, +81bps, monotonic conviction) BUT FAILS beats-random:
  random-timed SAME-direction (long-biased) entries earned +106bps vs the signal's +81. In a 5y bull
  market a long-biased daily signal's high t-stat is just MARKET DRIFT — the trend timing adds nothing
  (slightly negative). The random-baseline gate correctly caught beta-as-fake-edge. CAVEAT: trend
  edge is exit/convexity-driven (ride winners), which a fixed-horizon ENTRY event study can't measure
  — Stage-1 may be the wrong lens for this class. Broader insight: any long-biased daily signal needs
  BETA-ADJUSTED / market-neutral evaluation, else drift masquerades as alpha. Fork (Owner): (a) test
  trend the right way — full strategy w/ trailing-stop exit, evaluated NET of buy-and-hold beta; or
  (b) pivot to a market-NEUTRAL signal class (cross-sectional / pairs) so beta can't confound.

- **Pullback-in-trend signal (Owner steer: per-name directional 'buy range' + naked calls/puts):**
  RSI(2) oversold in an uptrend (buy dip) / overbought in downtrend (buy put). **FIRST signal to BEAT
  the beta baseline** — h=3: +28bps vs random +9, t=3.28, beats-random TRUE (RSI2<15: h=3-10 all beat
  random). Genuine short-term reversion ALPHA (dips bounce better than random long entries; breakouts
  did not). BUT edge is SHORT-LIVED (3-5d, fades by h=10-20 as drift dominates) and hit ~0.52 (mean
  effect, not hit-rate) → FAILS the strict gate at primary h=10. KEY: the linear forward-return event
  study UNDERSTATES convex naked-long-option payoffs (few big bounces pay for many small premium
  losses) — so the real arbiter for the user's chosen product (naked calls/puts) is the actual ORATS
  single-leg option P&L, not the linear test. NEXT: build single-leg long call/put reconstruction +
  test the pullback signal on real ORATS.

- **Naked call/put test on pullback (real ORATS) = LOSES (2026-06-15):** 443 trades, 5d hold, ~ATM
  ~30 DTE: win 40%, mean -4.7% / median -19% on premium, ann Sharpe -1.65, PF 0.84 (best +503% /
  worst -98% — convexity present but insufficient). Avg underlying move +0.19% over 5d. ROOT CAUSE:
  the pullback edge is a ~0.2-0.3% bounce; options frictions (premium/theta/bid-ask) need multi-%
  moves. DEFINITIVE, EXHAUSTIVE CONCLUSION: across squeeze+RAF (2h/4h/1d), squeeze-only, weekly-aligned
  daily, trend/breakout, pullback-reversion — and options as verticals AND naked calls/puts — every
  options structure loses because the real price-based edges on these 54 large caps are small (~0.2-
  0.5%) while options need several %. The options thesis is NOT supported by price-based signals on
  this universe. Defensible products = the STOCK signals (2h squeeze ~+1.5 Sharpe; pullback short-term
  reversion as stock, beta-beating at h=3-5). For an options product you'd need bigger-move sources:
  higher-vol/lower-priced names, or catalyst/earnings signals (need event data) — new scope.

- **Trend-strength filter + paper forward-test scaffolding (2026-06-16):** added a momentum-agreement
  filter to the pullback signal (`trend_mom`: long needs ret(63)>0, short <0) — screens 'below-SMA-but-
  momentum-up' marginal setups. Re-validated: at the correct horizon (h=5) the filtered pullback
  decisively BEATS the beta baseline (e.g. trend_ma50: +42bps vs random +11, t=3.99) across settings;
  the filter lowers the random baseline (cleaner entry selection) and drops momentum-disagreeing names
  (e.g. DIS). Still hit-rate-marginal (0.52<0.53) — a mean/convexity edge, not high-win-rate; adopted
  for forward-testing (trend_mom=63). Built Tradier SANDBOX integration (reporting/tradier.py +
  reporting/paper_trade.py — preview-by-default, refuses non-sandbox) and scripts/daily_paper.sh
  (cron: refresh→recs→paper orders→log). Paper forward-test AWAITS Owner sandbox creds in .env.
  NOTE: earlier MA-short writeup said its 3mo momentum was +1.9% (positive) — that was an off-by-one;
  the signal's actual 63d momentum is -1.7% (negative), so MA IS in a downtrend and the short is
  consistent (filter keeps it). Forward-test is evidence-gathering only — nothing graduated.
- **Paper orders PLACED + shared-account tracking (2026-06-16):** Tradier sandbox verified (acct
  VA00000000, $100.5k paper). Placed 12 paper orders (6 equity edges + 6 observe-only options), ids
  32806797-32806826, accepted (pending → fill next session). **SHARED-ACCOUNT GUARDRAIL (Owner): only
  read/track the order ids THIS system placed; NEVER cancel/modify/touch any other order; do NOT use
  the commingled positions endpoint for attribution.** Local ledger (reporting/paper_ledger.py →
  data/paper_orders.jsonl, our ids only) + read-only tracker (reporting/track.py: per-order P&L from
  our fills + live quotes → data/paper_pnl.jsonl). Backfilled the 12 ids; daily_paper.sh places+tracks.

- **Exit strategy implemented (2026-06-16):** the placed entries had no exits — gap closed with an
  exit manager (reporting/exit_manager.py). Per filled position WE opened: (1) attach a RESTING GTC
  stop — equity 2.5×ATR(14) from fill, option 50% of premium (exchange-side 'bracket' stop); (2) CLOSE
  on time-stop (squeeze_2h ~2 trading days, pullback_1d ~5) OR signal reversal, cancelling our stop.
  Strictly scoped to our ledger ids (never other orders, never the positions endpoint). Tradier OTO
  brackets validated but require limit entries; chose resting-stop-on-fill (works with the placed
  market entries, simpler, same protection). daily_paper.sh now runs exit_manager→paper_trade→track.
  Currently a no-op (12 orders pending; acts once filled next session). Holds match the validated
  backtests; intraday stop precision is exchange-side (resting), time-exit is daily-cadence.

- **Market-agnostic test (residual edge-check) = NO idiosyncratic alpha (2026-06-17):** Owner wanted to
  remove market-beta noise (hedge / beta-neutral recs / residual signals). Built signals/market.py
  (beta, residual 'beta-neutral price') + validation/residual_edge.py. Result: the pullback edge is
  REAL in RAW returns (h=3-5 t≈3, beats random) but **VANISHES in RESIDUAL (market-neutral) returns**
  (h=3 t=-0.84, h=5 t=0.74 — never beats the baseline). CONCLUSION: the apparent edge is largely BETA
  (riding short-term MARKET reversion), not stock-selection alpha. Removing the market noise also
  removes the return → a hedged/beta-neutral version of these signals would hover ~0 minus costs.
  Did NOT deploy the SPY hedge / beta-neutral rec sizing (would be a ~zero-edge book — misleading).
  A genuinely market-agnostic strategy needs a signal with idiosyncratic edge (earnings/catalyst,
  pairs/sector-relative, fundamental cross-section) — not these price-reversion/breakout rules.

- **Pairs / sector-relative (market-neutral) — promising but underpowered (2026-06-17):** built
  signals/pairs.py (rolling-hedge-ratio spread + z) + validation/pairs_edge.py (forward DOLLAR-NEUTRAL
  spread returns vs random). BEST market-neutral result so far: 39 same-sector pairs, entry |z|>2 →
  beats the random baseline at EVERY horizon with the right (reversion) sign, strengthening at longer
  horizons (h=10 t=1.42 +33bps; **h=20 t=1.77, hit 0.53, +59bps**). z>1.5 DILUTES it (t=0.21) — a good
  sign it's a real extreme-only effect, not noise. BUT still **fails the gate (t<2)** — 697 entries /
  5y / 39 pairs is underpowered. Unlike the single-name residual test (zero edge), pairs shows a
  genuine idiosyncratic reversion tendency. To CONFIRM needs power: a broader cointegration-screened
  pair universe + more history (>5y) — a data/scope expansion. Did NOT optimize (rule 3: Stage-1 not
  cleanly passed). This is the most viable market-agnostic lead.

- **MA-pullback entry (Owner hypothesis: enter at 20 SMA / 21 EMA) = does NOT validate as a swing
  entry (2026-06-18):** built signals/ma_pullback.py (touch-and-hold of rising 20SMA/21EMA in uptrend,
  0.25*ATR touch, >50MA filter) + edge-check raw+residual. RAW: slightly + at h=3-5 but strongly
  NEGATIVE at the swing horizon (SMA20 h=20 mean -71bps t=-4.16; EMA21 t=-4.39) — pullbacks to the MA
  were followed by further weakness over 2-4wks across the broad 2021-26 universe (mixed regimes /
  drawdowns broke the MA). RESIDUAL: only a faint 3-day idiosyncratic bounce off the 20SMA (h=3 t=2.39,
  +23bps) — too short/small for swing/options. Conclusion: not a usable swing/options entry on this
  universe/period; the technique is regime-dependent (works in strong persistent uptrends). Possible
  refinement: much stronger trend filter (>200MA, 50>200, high ADX) to isolate clean trends — untested.
- **MA-pullback strong-trend retest (2026-06-18) = WORSE, hypothesis not supported:** gating to clean
  trends (>200MA, 50>200, ADX rising) made it MORE negative (EMA21 strong raw: h=20 -247bps t=-2.40,
  152 entries) — a deep MA pullback inside an already-strong high-ADX uptrend marks deceleration/tops,
  not continuation. Residual+strong-trend ≈ 0 entries (the "strong trend" is mostly beta). FINAL: the
  MA-pullback doesn't validate at any config; only a faint 3-day residual bounce off the 20SMA (t=2.39).
  Meta: across squeeze/RAF, RSI-pullback, breakout, MA-pullback, single-name residual — simple
  price-based entries on this large-cap 2021-26 set don't yield robust market-neutral swing edge;
  pairs is the one (underpowered) market-neutral lead.
- **Pairs done properly (cointegration screen + OOS) = underpowered, inconclusive (2026-06-18):**
  broadened to an 82-name sector-grouped pool (config universe.pairs_pool; pulled 1d), Engle-Granger
  screened WITHIN sectors on train (<2024-06): only 6 pairs passed (p<0.05, HL 2-40d) — NVDA-LRCX,
  NOW-INTU, V-MA, JNJ-PFE, JNJ-BMY, DE-MMM. OOS test (after 2024-06): positive sign (+34/+37/+47 bps
  at h=5/10/20) but only 47 trades, t≈0.3-0.5 → NOT significant. Cointegration was the right method
  but slashed candidates 238→6; 82 names is too few to power a pairs test. To confirm/kill needs a
  MUCH larger universe (hundreds of names → thousands of candidate pairs → a real data project), not a
  tweak. statsmodels added (optional). Forward-test (#3): V/PG exits placed at 2-day hold; 4 pullbacks
  (MA/KO/PEP/HON) open until ~06-23. NET RESEARCH STATE: no price-based or market-neutral signal on
  this universe yields a confirmed, options-viable edge; pairs is sign-right but unprovable at this scale.

- **Pairs at scale = KILLED, now powered (2026-06-19):** built a 400-name top-liquid common-stock
  universe (grouped-daily dollar-volume rank, ETF-excluded; config/liquid_universe.json), pulled 1d for
  all (383 with >300 bars). correlation_candidates(min_corr=0.80) → 281 candidate pairs → Engle-Granger
  screened on train (<2024-06): 18 cointegrated (p<0.05, HL 2-40d). OOS test (158 entries — properly
  powered now): NEGATIVE — h=5/10/20 mean -13.8/-32.9/+11.3 bps, t -0.50/-0.83/+0.19, loses to random
  at every horizon. The 82-name "+sign lead" was noise; at scale spread mean-reversion does NOT persist
  OOS on liquid large-caps. Pairs/stat-arb is DEAD on this universe. (Method sound; the anomaly isn't here.)
- **PEAD catalyst signal = NULL, well-powered (2026-06-19):** pulled quarterly filing dates from Polygon
  financials for 326 names (~5,477 events; signals/pead.py detect_events finds the announcement day as
  the volume-spike day in [filing-45d, filing+2d], reaction = that day's signed return). Edge-check
  (validation/pead_edge.py) on 3,246 detected events: drift in the reaction direction is flat-to-negative
  RAW (h=10/20/40: -1.4/-25.5/-15.5 bps, t≈0) and RESIDUAL (market-neutral: -7.3/-15.8/+11.3, t<0);
  even the strong-surprise (|react|>3%) subset is noise (t<0.8). Consistent with the literature: classic
  PEAD has largely decayed in liquid large-caps (its surviving pocket is low-coverage small/mid-caps —
  opposite of our universe). Caveat: filing_date lags the press release for some names and ~some quarters
  have null filing dates, but the volume-spike detector + 3,246-event power make a real edge unlikely here.
- **NET RESEARCH STATE (2026-06-19): alpha hypotheses exhausted on liquid large-caps.** Tested & rejected
  as market-neutral swing/options edges: squeeze+RAF confluence, squeeze-only, RSI-pullback, breakout,
  MA-pullback (21EMA/20SMA, incl. strong-trend), single-name residual, pairs/stat-arb at scale, PEAD
  catalyst. The ONLY confirmed edge is the small 2h squeeze (real but ~+23bps — too thin for options
  frictions; tradeable only as equity). Discipline (random baselines, residual returns, OOS cointegration,
  walk-forward, powered samples) repeatedly separated beta/noise from alpha. Realistic paths left: (a) trade
  the thin 2h squeeze as equity-only; (b) change the hunting ground — small/mid-cap universe (where PEAD
  still lives) or a different asset class, both new data projects; (c) let the forward-test (#3) be arbiter.

- **2h squeeze as equity = does NOT graduate; forward-test only (2026-06-19, user chose "trade the
  2h squeeze as equity"):** ran the full squeeze-only 2h funnel with EQUITY costs. Stage-1 edge real
  (11,653 fires, +8.2bps, t=3.33) and WFA efficiency 2.10 (mean test Sharpe 0.81), but the graduation
  gauntlet FAILS: OOS Sharpe -1.42, research 0.75, DSR 0.82, bootstrap median 0.75, permutation 73.8pct,
  subperiod unstable (2021 -2.48 ... 2025 +1.63). Per CLAUDE.md rule 5 we do NOT move goalposts or call
  it graduated. DECISION: operationalize it equity-only as forward-test EVIDENCE, not deployment.
  paper_trade.py is now equity-only by default (options behind --with-options); daily cron unchanged.
  The +8bps event-study signal is a recent-years phenomenon that doesn't convert to a Sharpe-1.2 timed
  strategy. No graduated strategy exists; the paper book is the live arbiter.

- **Cross-sectional family opened up (2026-06-21):** built validation/xsec_edge.py — dollar-neutral
  decile L/S on the 383-name liquid universe, costs on, 1-bar lag, vs random-rank. RESULTS: momentum
  12-1 SR 0.69 (+18%/yr, t1.36, beats random) — FIRST market-neutral signal with a pulse; reversal_1wk
  dead (-0.58); overnight-only SR 1.16 t2.56 (real anomaly: 62% of buy-hold return at 60% of vol) BUT
  carries market beta and ~25%/yr turnover cost kills the tradeable MOC/MOO version. Momentum chosen to
  deepen.
- **Upgraded momentum gauntlet = FAIL, but signal is REAL (2026-06-21):** signals/xsec_momentum.py
  (residual/beta-neutral momentum = raw 12-1 minus beta*market 12-1) + sector-neutral demean + inverse-vol
  legs; validation/momentum_run.py runs the full gauntlet on the portfolio monthly series (12-config grid).
  Result (pre-sector pull): research SR 0.73, PASSES permutation (98th pct — ranking beats random),
  OOS-24m SR 1.56, neighborhood 0.31. FAILS PBO 0.63, DSR 0.81, bootstrap median 0.76 / p5 -0.20.
  Subperiod 2022 +1.89 / 2023 -0.96 (momentum-crash year) / 2024 +0.32 / 2025 +1.9 / 2026 +4.62. READ:
  the edge is genuine (beats random, strong recent OOS) but not robust enough to graduate — killers are
  bootstrap median + PBO, both driven by SHORT SAMPLE (46 monthly obs) + the 2023 momentum crash. This is
  the one signal where "the window is the problem, not the idea" is literally true (literature graduates
  momentum on decades; we have ~5y w/ a crash). Sector-neutral re-run pending data pull. Data pull note:
  Polygon ticker-details loop hit local ephemeral-port exhaustion (ConnectError errno 49) — use a pooled
  httpx.Client w/ backoff for bulk reference pulls.
- **Momentum sector-neutral re-run = confirms FAIL, signal real (2026-06-21):** sectors pulled
  (data/sectors.json, 311/400, 2-digit SIC). Sector-neutral demean IMPROVED the point estimate —
  research SR 0.73->0.88, Stage-1 t 1.44->1.72, permutation 98->99th pct; chosen lookback252/decile0.2/
  volscale True. Still FAILS: bootstrap median 0.89 / p5 -0.04, PBO 0.46, DSR 0.81, neighborhood 0.04;
  OOS-24m SR 1.45 (pass). Subperiod 2022 +2.73 / 2023 -0.64 / 2024 +0.35 / 2025 +2.13 / 2026 +4.81.
  VERDICT LOCKED: residual x-sec momentum is a genuinely real market-neutral edge (beats random 99th
  pct) but UNPROVABLE on 5y w/ the 2023 crash; every upgrade made it cleaner, none flipped the gauntlet.

- **CLEAN-UNIVERSE GAUNTLET = near-graduation; contamination was HURTING the edge (2026-06-23):** re-ran
  validation/momentum_run.py --clean (point-in-time cleaning at each rebalance: must have SIC sector [drops
  ETFs], full lookback history, price>=$5, |12-1 ret|<=300%, |beta|<=3). Result DRAMATICALLY better than the
  raw 383-name run — the ETF/split-artifact noise had been dragging the signal DOWN, not propping it up.
  Clean: research SR 0.88->1.45, Stage-1 t 1.72->2.85 (mean 84bps/mo), OOS-24m SR 2.23, PBO 0.46->0.10,
  bootstrap median 0.89->1.48 / p5 -0.04->0.69, permutation 100th pct. Now PASSES 6 of 8 gates; FAILS only
  DSR 0.944 (need 0.95, by 0.006) and neighborhood min 0.269 (need 0.30, by 0.03) — both by a hair. Chosen
  config flipped to volscale=FALSE (vol-scaling HURT; the value came from residual+sector-neutral). Subperiod
  2022 +1.68 / 2023 -0.26 / 2024 +1.91 / 2025 +2.74 / 2026 +2.2. DISCIPLINE: still a FAIL under locked gates;
  do NOT tweak the grid (e.g. drop volscale) post-hoc to push neighborhood over — that's goal-hacking. The
  borderline DSR on 46 monthly obs is within sample noise. VERDICT: residual x-sec momentum on a CLEAN
  universe is the strongest, most graduation-worthy signal in the project — real, market-neutral, nearly
  certified — but still short of the full bar on 5y. Confirms more history is the unlock, not more tweaking.

- **VERDICT GATING/SORTING ENABLED in the screener (2026-07-02, user-approved):** the screener freeze is
  lifted for this one change, on the strength of validation/verdict_backtest.py (GO 55.7% win/+105bps t4.72
  vs AVOID 45.1%/−40bps on 2,465 setups; tape-gating arm 3,998 setups, support 55.1% vs fight 48.1%) and the
  catalyst backtest (opposed news 42-43% win, t −2.5/−3.4). Changes: (1) matrix sorts by verdict rank then
  conviction; (2) default-HIDE opposed-news and AVOID rows (toggles to reveal; AVOID rows dimmed when shown);
  (3) squeeze_scan --alerts annotates verdict and does NOT push AVOID alerts to ntfy — FAIL-OPEN if the tape
  score is unavailable (verdict '?', still pushed) so a dead FRED feed can't silence real setups. Scan/tier/
  direction logic (screener_core) UNTOUCHED. Caveats logged: ~23mo sentiment window, one regime, not
  walk-forward — this gates a DISCRETIONARY screener, it does not graduate an auto-trade signal.

- **CONFLUENCE SCORE FAILED its pre-registered gate → demoted to display-only (2026-07-02):**
  validation/confluence_backtest.py, gate pre-registered in gates.yaml (committed BEFORE the run). 5,994
  daily EW setups; confluence = # of multiday TFs (2d/3d/4d/1w) with a same-direction tier-grade cell as-of
  the setup date. RESULT: conf>=1 was WORSE than conf=0 at 10d (47.8% win/−345bps vs 50.8%/+15bps; lift
  −2.0 pts vs required +3.0; diff-t −1.24 vs required 2.0). ALERT+conf=1 was the worst bucket (38.7% win).
  ACTIONS per the pre-registered on_fail: (1) conf bonus REMOVED from decision.verdict scoring (the
  validated verdict_backtest config used conf_n=1 anyway — the live bonus was never validated); (2) matrix
  ranks by verdict+conviction alone; Conf column stays visible as information, not ranking. NOTED, NOT
  ACTED ON (would be post-hoc): conf>=2 bucket was mildly positive (51.4%, t 2.54, n=142) — if ever worth
  pursuing, pre-register a NEW gate for "2+ multiday confirmations" first. CAVEATS: intraday TFs untested
  (no history); overlapping setup-days.

- **HOLD-HORIZON SWEEP: no qualifying horizon → defaults unchanged (2026-07-02):**
  validation/hold_horizon_sweep.py, rule pre-registered in gates.yaml before the run. 5,995 daily EW
  setups: the edge is FRONT-LOADED — 0→3d is the only positive incremental window (+7.8bps, t 1.55);
  5→20d adds ~nothing (7→10d −16bps). Pick 3d failed the cumulative-t bar (1.55 < 2.0) → per the rule,
  keep current hold defaults. SIDE FINDINGS (not acted on, each needs its own pre-registered test):
  (1) ALERT-only is NEGATIVE at every horizon (10d −72bps, 20d −99bps t −2.1) and WORSE than the all-
  setups baseline — the momentum-confirmed tier may be chasing; tape/verdict support (which passed) is
  doing the real work, not the tier. Follow-up hypothesis: gate ALERT pushes on tape-support only.
  (2) The full-5y 10d baseline is ~flat (−7bps) vs +47bps t3.2 in the ~23mo sentiment-window sample
  (catalyst backtest) → the raw squeeze edge is regime-dependent; recent-window results should be
  discounted accordingly.

- **EARNINGS-PROXIMITY: no penalty found → no screener change (2026-07-03):**
  validation/earnings_proximity_backtest.py, gate pre-registered in gates.yaml before the run. 6,036
  daily EW setups, 372 with earnings 1..10d ahead: win-rate gap vs control −0.5 pts @10d (trigger needed
  −3.0), diff-t +0.72 (needed −2.0) → NOT TRIGGERED. Pre-earnings squeeze setups are NOT a loser bucket;
  if anything 1..5d-pre looks mildly (insignificantly) better (54.6% win). Post-earnings 0..5d slightly
  weak, insignificant. NO filter added. POWER caveat: n=372 rules out a large penalty, not a small one;
  earnings coverage imperfect (missing dates dilute control, biases against the effect). trade_plan's
  earnings_in_window flag stays as-is (informational, options-vol relevance unaffected by this test).

- **ALERT-PUSH-RULE TEST: NOT TRIGGERED → push rule unchanged (2026-07-03):**
  validation/alert_push_rule_backtest.py, gate pre-registered (disjoint disagreement region: A=WATCH+
  tape-support (candidate-only pushes) vs B=ALERT+tape-fight (current-only)); hypothesis DISCLOSED as
  data-derived from the hold-horizon side finding. RESULT @10d: A won 55.8% (n=2056, +39bps) vs B 47.4%
  (n=612, −60bps) — win-gap +8.4 pts (bar 3.0 ✓) but mean-diff Welch t 1.21 < 2.0 ✗ → NOT triggered (both
  conditions required). HONESTY NOTE: a win-rate proportions z-test would likely have passed (~3.7), but
  the registered metric was mean-diff t; switching metrics post-hoc = goal-hacking, so NO CHANGE. The 2×2
  keeps corroborating that ALERT tier is weak (negative mean bps in every ALERT bucket at 10d) and
  WATCH+strong-support is the best cell (56.9% win; 3d t 4.06). If this is ever revisited, pre-register a
  NEW gate (metric chosen blind, e.g. on a fresh data window) — candidate sharper hypothesis: suppress
  ALERT+tape-fight pushes only.

- **GEX BACKTEST: BOTH GATES FAIL → conviction adjustments REMOVED (2026-07-04):**
  validation/gex_squeeze_backtest.py with REAL historical OI from the local ORATS archive
  (ORATS_ARCHIVE_DIR, 2007-2026 full history; window used 2025-01..2026-06, 362 days, 1,908 setups —
  ~13x the spec's feared n=149). Gates pre-registered (incl. the disclosed 10d-vs-spec-15d deviation).
  H1 FAILED AND REVERSED: NEG-gamma setups (the live "+8 breakout tailwind") won 47.5% @10d vs POS
  53.3% — gap −5.8 pts, bootstrap p=0.981. The dealer-amplification story was backwards for our squeeze
  pool; the ±8 was actively subtracting skill. H2 (wall headroom) directionally right (+4.0 pts,
  57.8% vs 53.8%; 15d even stronger 60.8% vs 53.1% t4.48) but missed the bar (need +5.0, p<0.05) →
  −6 wall penalty removed too. ACTIONS: decision.verdict no longer scores GEX at all; walls/flip/
  profile stay as DISPLAY LEVELS in /internals + trade_plan (level-pinning targets unchanged — that
  use was not gated here). NOT acted on (would be post-hoc): flipping the regime sign (POS-gamma
  favoring squeezes is plausible — pinning helps coil retests — but needs its own pre-registered gate,
  ideally on the pre-2025 archive years as fresh data). Infra note: orats_adapter now reads the local
  Google Drive archive (no S3 creds needed); gex_hist.py mirrors reporting/gex.py math exactly.

- **NEWS STALENESS BUG FIXED — live now matches the validated 6-7d window (2026-07-04):** discovered in
  the quality study (docs/quality_improvement_study.md): the news fetchers took last-10 articles with NO
  date floor, so thin-coverage names carried badges from months-old headlines (measured: ROP newest 46d,
  span to 162d — its +10 "news aligned" at entry was stale). The VALIDATED catalyst backtest used a ~6d
  sentiment window. Fix: sentiment badge counts only articles < 7d old (webapp fetch_news_async +
  squeeze_scan._news_sentiment); headlines list unchanged for display, items get a `fresh` flag. Treated
  as a bug fix toward the validated configuration (no new gate). Effect: no-recent-news names go NEUTRAL
  (no factor) instead of inheriting stale sentiment. Also measured: median ticker has sentiment on only
  19% of days; 25% of ticker-days are pos==neg ties — the news factor is silent for ~4/5 names on any day.

- **TIER RE-WEIGHT ADOPTED: ALERT bonus 15 → 4 (2026-07-04):** validation/tier_weight_test.py, rule
  pre-registered (3 candidates {15,4,0}, one free parameter, n=4,040, selection = best GO-bucket 10d
  win% with GO n>=200 and >=0.5-pt gain). RESULT: bonus 4 → GO 56.3% win/+106bps (n=1,308) vs current
  15 → GO 55.0%/−7bps (n=1,532); bonus 0 similar (56.1%) but 4 won and preserves a small
  setup-present credit. The +15 had been promoting ALERT setups with marginal tape into GO — consistent
  with the three prior looks showing ALERT-tier weakness. decision.verdict updated; tier label still
  displayed. Fourth consecutive piece of evidence that tape >> tier.

- **ARCHIVE FACTORS (P/C alignment, IV-rank): both FAIL gates — registered, NOT wired (2026-07-04):**
  validation/archive_factors_backtest.py, gates pre-registered. n=1,930 setups, factors from the owned
  ORATS archive (data/orats_factors.parquet snapshot cache). F1 positioning-alignment: +3.8 win-pts
  (53.9% vs 50.1%) p=0.049 — significant but under the +5.0 bar; mean spread large (+311 vs −342bps,
  t 2.42/−2.00). F2 IV-rank: +3.4 pts p=0.086. Both directionally right on an 18-month window = honest
  near-misses. DESIGNATED FOLLOW-UP (not a re-roll of the same data): materialize PRE-2025 archive years
  and re-test on genuinely fresh setups with the SAME gates; adopt only if fresh data clears the bars.
  Per on_fail: nothing wired into verdict.

- **ARCHIVE FACTORS FRESH-DATA RE-TEST (pre-2025 years, 2026-07-04): F1 KILLED, F2 replicated-but-under-bar
  — still nothing wired:** after materializing 2021-08..2024-12 from the ORATS archive (860 days; cache
  now 1,222 days/12GB; factor cache 382k snapshots), re-ran the SAME registered gates on 3,932 fresh
  setups. F1 P/C-alignment REVERSED: −2.0 pts (48.9% vs 50.9%), p=0.887 → the 2025-26 near-miss (+3.8,
  p=0.049) was sample noise; hypothesis DEAD, do not revisit without a new mechanism story. F2 IV-rank
  REPLICATED in direction and size: +3.0 pts (51.0% vs 48.0%), p=0.038 — two independent windows both
  ≈+3 pts @10d; strongest at 3d (54.0% vs 45.2%, t 3.32/−1.68, +8.8 pts) where the squeeze edge lives.
  Under the registered +5.0 @10d bar in BOTH windows → NOT wired. If ever pursued: a NEW pre-registered
  gate at the 3d horizon on data neither window touched (needs pre-2021 equity bars → Norgate).

## How to override
Change the row here, note it in the log with date + rationale, and let the rest of the spec
re-flow. `CLAUDE.md` rules override convenience; do not silently deviate.
