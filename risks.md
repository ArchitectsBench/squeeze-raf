# Risk Register

Top failure modes and their mitigations. Claude Code: when you hit or retire a risk, update
Status and add a dated note. New risks discovered during the build get appended.

| #  | Risk | Impact | Mitigation | Status |
|----|------|--------|-----------|--------|
| R1 | Look-ahead via RAF displaced TEMA (use `shift(-14)` instead of `shift(14)`) | Fake edge — total | Backward-shift only; dedicated unit test that fails on forward shift | Open |
| R2 | Survivorship bias in SPX-500 universe | Inflated Sharpe | Point-in-time constituents (D6), or log the caveat | Open |
| R3 | Selection bias from testing many configs | Sharpe looks better than it is | Deflated Sharpe + PBO mandatory at graduation, not optional | Open |
| R4 | Confusing underlying-signal Sharpe with spread Sharpe | Recs underperform the backtest | Validate the spread layer separately on real IV data (P8) | Open |
| R5 | Illiquid option chains | Backtest/recommended fills are fiction | Curate universe by option liquidity (open interest + spread) | Open |
| R6 | Knife-edge optimum | Live degradation | Parameter-neighborhood test + plateau preference | Open |
| R7 | Intraday data gaps / Alpha Vantage rate limits | Silent holes in data | Integrity test in P1; Polygon/Databento for full-universe scale | Open |
| R8 | Agent goal-hacks the gate (tunes to holdout, moves thresholds) | Believable but overfit result | Lock OOS in config, pre-register thresholds, test-first, stop-at-gate | Open |
| R9 | Leveraged-ETF decay / reverse splits (UVXY, VXX, SOXL…) | Skewed long tests | Split-adjust; decay-aware handling of vol ETFs | Open |
| R10 | Indicator port drifts from ThinkOrSwim | Trades a different rule than intended | Stage-0 fidelity tests vs ToS fixtures before any backtest | Open |
| R11 | Data-vendor cost creep / lock-in | Budget blowout at P8 | Start cheapest tier with 2y IV+greeks for the shortlist; scale after proof | Open |
| R12 | Paper-vs-live slippage gap | Live underperforms paper | Conservative slippage in backtest; Tradier-sandbox forward test (P9) before real capital | Open |
| R13 | Regime shift after deployment | Edge decays | Subperiod stability check at P6; monitor live vs expected; periodic re-validation | Open |

## Notes
- R1, R8, R10 are the ones an unsupervised agent most easily gets wrong — they are why
  `CLAUDE.md` mandates test-first on look-ahead + fidelity and a locked holdout.
