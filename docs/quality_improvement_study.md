# Recommendation-Quality Improvement Study

**Date:** 2026-07-04 · Status: discovery complete, roadmap at end.
Question: how do we improve recommendation quality and conviction — is our news feed
good, and what integrations/data feeds would raise quality?

---

## 1. Audit: what conviction is actually made of today

`decision.verdict` = 50 + tape·0.6 ± tier ± news ± intraday·0.2. Evidence status:

| Input | Weight | Evidence | Verdict |
|---|---|---|---|
| Tape score (breadth+vol+credit) | ±30 (0.6×tape-50) | PASSED pre-registered gate (support 55.1% vs fight 48.1%, n=3,998) | **The workhorse.** Carries most of the real signal |
| Tier ALERT/WATCH | +15 / +4 | 3 independent looks show ALERT is WEAK-to-negative (10d −72bps ALERT-only; every ALERT bucket negative) | **Overweighted.** Likely the biggest mis-weight in the stack |
| News aligned/opposed | +10 / −25 | PASSED (opposed 42-43% win, t −2.5/−3.4) — but see §2: live implementation diverges from validated form | Real, but implementation bug dilutes it |
| Intraday breadth | ±10 | Unvalidated (no history to test — proxy built from 5m bars) | Unknown; small weight, acceptable |
| Confluence | 0 (removed 07-02) | FAILED gate | Display-only ✅ |
| GEX regime/walls | 0 (removed 07-04) | FAILED gate (sign was backwards) | Display-only ✅ |

**Conviction improvement #1 (no new data needed): re-weight the tier factor.**
The +15 ALERT bonus contradicts three backtests. A pre-registered re-test of tier
weights (e.g. ALERT +0..+5) on the existing sample would likely raise GO precision.

**Conviction improvement #2: the verdict has never been jointly optimized.** Weights
(0.6, 15, 4, 10, −25, 0.2) were hand-set, then validated as a whole. A walk-forward
logistic fit on the validated inputs (tape, news, tier) with the standard gauntlet
would put the weights on evidence footing. Risk: overfitting a 23-month window —
needs the deeper history (§4) to do properly.

## 2. News feed evaluation — Polygon `/v2/reference/news` + `insights`

Empirical (2024-07 → 2026-06, 329 tickers, 45,673 ticker-days with news):

- **Coverage is thin and skewed:** median ticker has sentiment on **19% of days**
  (p10 tickers: 5%). On any given day, the news factor contributes NOTHING for ~4 of
  5 names. Mega-caps (p90: 64% of days) dominate.
- **25% of ticker-days are uninformative** (pos==neg ties).
- **History starts 2024-07** (Polygon insights began ~Aug 2024) — the binding
  constraint on every news-conditioned backtest (23 months, one regime).
- **LIVE BUG FOUND (fix immediately):** the API/webapp fetcher takes the last 10
  articles with **no date filter**. Measured on 10 matrix names: ROP's "newest"
  article was 46 days old (oldest 162d); CB 44d; MARA 32d. The +10 "news aligned"
  ROP carried at entry was months-stale. The VALIDATED form (catalyst backtest)
  used a **6-day window**. Live must filter `published_utc >= now-7d` (and treat
  no-recent-news as neutral) to match the validated configuration.

**Verdict on the feed:** adequate for mega-caps, structurally thin for the mid-tier,
short history. Worth upgrading — but fix the staleness bug first; it's free and
probably worth more than a new vendor.

## 3. Alpha Vantage MCP — hands-on result

Connected and callable in-session, but the shared key is free-tier: **25 requests/
day** (exhausted during testing). Unusable beyond toys for a 330-name universe.
Premium (~$50/mo, 75 req/min) would unlock:
- `NEWS_SENTIMENT` — per-ticker scored sentiment with `time_from` back to ~2022:
  potentially +2.5 years of sentiment history vs Polygon (validation power).
- `HISTORICAL_PUT_CALL_RATIO` — **per-name P/C ratio back to 2008.** The standout:
  a positioning factor with enough history to run the full gauntlet (unlike GEX,
  which died partly for want of history).
- Insider transactions, earnings calendar (would fix our gappy earnings file).

## 4. External providers & connectors (verified July 2026 unless flagged)

### 4a. The insight that reorders everything: we already own the best dataset
The ORATS SMV archive (2007-2026, local) contains per-strike **cVolu/pVolu/cOi/pOi
and IVs** — i.e. per-name put/call ratios, IV rank/percentile, and term structure are
computable for FREE with 19 years of history. Every paid "positioning" product below
should be judged against that baseline. Backtest in-archive signals BEFORE buying.

### 4b. Options flow / positioning (agent research, verified)
| Provider | Price | Verdict for us |
|---|---|---|
| Unusual Whales API | ~$125-150/mo (+$40 one-time trial) | Only NEW signal class (flow alerts, market tide, dark pool). UOA edge typically decays intraday — questionable at 3-15d. Do the $40 trial only after free factors are tested |
| ORATS Data API | $99/mo (delayed, 20k req/mo) | Cleanest paid option IF we want ongoing daily SMV freshness + precomputed IV rank. Archive covers history already |
| CBOE All Access | $2,499+/mo | Out of budget. But FREE: daily P/C stats page + CSV archives (2006–2019-10) |
| SqueezeMetrics | free CSV (DIX + index GEX, 2011+); live $720/mo | Free CSV = canonical INDEX-level dealer-positioning series — backtestable as a tape-score component |
| SpotGamma / Market Chameleon | $74-224/mo / $39-99/mo | No retail API — dashboards only, poor algo fit |
| Tradier production | **$0** with funded brokerage account | Real-time quotes + ORATS-fed greeks, 120 req/min. We get this anyway when going live |

### 4c. News/sentiment (main-session verification; agent died to session limits)
- **Alpha Vantage NEWS_SENTIMENT**: Plan 75 $49.99/mo (75 req/min) unlocks it at scale;
  history depth is NOT officially documented (community: ~2022) — VERIFY DURING A ONE-MONTH
  TRIAL before counting on it for backfill. Their official MCP exists but the shared
  free key (25 req/day) is unusable for our universe.
- Others (Benzinga/Tiingo/Finnhub/EODHD): unverified this pass (research agent hit
  session limits). Revisit only if the AV trial disappoints; Polygon+window-fix may be
  sufficient for live, with AV backfill for history.

### 4d. The structural gap: survivorship-free daily history
**Norgate Data US Platinum: USD 630/yr** (6mo $346.50) — daily US equities back to
1990 **including 25k+ delisted securities and historical index constituents**, official
`norgatedata` PyPI package. This is the canonical fix for the momentum-graduation
blocker (needs 10-15y point-in-time history) and would also extend every screener
backtest out of the current one-regime window. Alternative: Polygon Advanced $199/mo
(20y tick history, real-time) — 4x the cost for data we mostly don't need at swing
horizon.
- Short interest: FINRA publishes bi-monthly short interest free (unverified this
  pass); fine as a later, low-priority filter test.

## 5. Prioritized roadmap (each new signal pre-registered before adoption)

**P0 — free, done or this week**
1. ✅ DONE (2026-07-04): news staleness fix — sentiment badge now counts only <7d
   articles, matching the validated window. (Was silently scoring months-old news.)
2. Pre-register + run a tier re-weight test (the +15 ALERT bonus contradicts three
   backtests; likely the single biggest free conviction improvement).

**P1 — free, high info-value: mine the ORATS archive we already own (2007-2026)**
3. Per-name PUT/CALL ratio factor (cVolu/pVolu, cOi/pOi) — 19y of history = full
   gauntlet possible. Replaces any need for AV's P/C endpoint.
4. Per-name IV-RANK factor (percentile of ATM IV) — same source, same rigor.
5. Wall-headroom re-test + POS-gamma sign-flip hypothesis on PRE-2025 archive years
   (genuinely fresh data for hypotheses born in 2025-26).
6. SqueezeMetrics free DIX/GEX CSV (2011+) as an INDEX-level tape-score component —
   backtest as a tape ingredient, not per-name.

**P2 — the two purchases that matter**
7. **Norgate US Platinum, $630/yr**: survivorship-free daily history to 1990 →
   unblocks momentum graduation (the one near-live strategy) AND extends screener
   validation out of the single-regime window. Highest impact per dollar in this study.
8. Alpha Vantage Plan 75, $49.99/mo, ONE-MONTH TRIAL: verify NEWS_SENTIMENT history
   depth; if it truly reaches 2022, backfill and re-run the news gates over the 2022
   bear. Cancel if history is shallow.

**P3 — later / conditional**
9. Unusual Whales $40 API trial (flow/tide) — only after P1 factors are tested; UOA
   edge likely decays faster than our horizon.
10. Tradier production data (free with funded account) — comes automatically with
   go-live; gives real-time chains and removes the 15-min delay concern.
11. Real-time Polygon upgrades, short-interest feeds — only if a specific
   pre-registered hypothesis needs them.

## 6. Bottom line
The biggest quality gains available are FREE: fix what diverges from validated form
(done), stop over-crediting ALERT tier, and mine the 19-year options archive for
put/call and IV-rank factors with real statistical power. The one purchase that
changes the system's trajectory is Norgate ($630/yr) — it attacks the root
credibility problem everywhere in this project: too-short, one-regime history.
