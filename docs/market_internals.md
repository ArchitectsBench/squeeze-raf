# Market Internals — research & buildability (living doc)

**Goal:** catalog the best market-internals tools/methods for gauging overall market
direction (so screener setups can be taken/sized with the tape in mind), and assess
what we can build NOW from data/access we already have (Polygon + our 400-name universe
+ FRED/CBOE free data). Web + public/free sources only. Research first → then recommend
a Phase-2 monitoring build (no builds without explicit approval; screener is frozen).

**Mode:** single self-paced deep pass. Status log at the bottom.

Buildability legend: 🟢 build now (data in hand) · 🟡 needs a free pull (FRED/CBOE) · 🔴 needs paid/new data.

---

## 1. Breadth — "how many stocks participate" (the core direction tell)
Breadth measures whether a move is broad or carried by a few mega-caps — divergences
(index up, breadth down) are classic topping tells.

| Indicator | What it tells you | Build |
|--|--|--|
| **Advance/Decline line** (cumulative net advancers) | structural participation trend; A-D flattening while index rises = narrowing, risk | 🟢 |
| **% stocks above 50-day / 200-day MA** | trend health & leadership; >50% above 200d = healthy uptrend | 🟢 |
| **McClellan Oscillator** (19-EMA − 39-EMA of net advances) | tactical breadth momentum; zero-line crosses, divergences | 🟢 |
| **McClellan Summation Index** (running sum of the Osc) | the big-picture breadth trend (bull/bear regime) | 🟢 |
| **New highs − new lows** (52-wk) | leadership extremes; expanding new lows = deterioration | 🟢 |
| **Up volume / down volume** (& 90% days) | conviction behind the move; 90% down-volume days = capitulation/thrust | 🟢 |

**Buildability:** strongly 🟢. Polygon's **grouped-daily** endpoint returns OHLCV for the
*entire* US market in ONE call/day (we already use it). From that we can compute advancers/
decliners, A-D line, up/down volume, and net new highs/lows; and from our cached per-name
history, % above 50/200-MA and McClellan Osc/Summation. **No new data needed.**
Sources: [McClellan Oscillator (ChartSchool)](https://chartschool.stockcharts.com/table-of-contents/market-indicators/mcclellan-oscillator),
[McClellan Summation](https://chartschool.stockcharts.com/table-of-contents/market-indicators/mcclellan-summation-index),
[mcoscillator.com breadth data](https://www.mcoscillator.com/market_breadth_data/),
[Three breadth signals (StockCharts)](https://articles.stockcharts.com/article/three-breadth-signals-that-help-confirm-market-trends/).

## 2. Volatility regime — "what the options market expects"
Direction context from fear/complacency and the shape of expected vol.

| Indicator | What it tells you | Build |
|--|--|--|
| **VIX level** | 30-day implied vol; >~20 caution, >50 = near intermediate bottoms (inverse to SPX) | 🟡 (FRED VIXCLS free) |
| **VIX term structure** (VIX9D / VIX / VIX3M / VIX6M) | contango = calm/risk-on; **backwardation (VIX9D>VIX>VIX3M) = stress/risk-off** | 🟡 (CBOE/FRED) |
| **Put/Call ratio** (CBOE total & equity) | sentiment extreme; spikes = fear (contrarian bottoms) | 🟡 (CBOE free) |
| **VVIX** (vol-of-vol) | stress in the vol market itself; tail-risk hedging | 🟡 (CBOE) |

**Buildability:** 🟡 — VIX & term structure are **free from FRED** (VIXCLS) and CBOE
(VIX9D/VIX3M); put/call from CBOE's daily files. A small daily pull (no paid plan) gets us
the level + term-structure ratio + put/call. Polygon Indices (I:VIX) may also work depending
on plan — to verify. Key signal to compute: **term-structure ratio (VIX9D/VIX3M)** as a
clean risk-on/off switch (backwardation = defensive).
Sources: [Cboe VIX term structure](https://www.cboe.com/tradable-products/vix/term-structure/),
[FRED VIXCLS](https://fred.stlouisfed.org/series/VIXCLS),
[Put/Call ratio (Britannica Money)](https://www.britannica.com/money/put-call-ratio).

## 3. Credit & macro risk — "is the smart money de-risking?"
Credit and macro markets often lead equities — they price stress before stocks do.

| Indicator | What it tells you | Build |
|--|--|--|
| **HY credit spread (OAS)** — FRED `BAMLH0A0HYM2` | corporate default risk; **widening = risk-off** (inverse to stocks); the single best macro confirm | 🟡 free FRED (daily, ~1d lag) |
| **2s10s yield curve** — FRED `T10Y2Y` | growth/recession lead (leads stocks ~1–1.5y); inversion → late-cycle caution | 🟡 free FRED |
| **Dollar (DXY)** | strong USD = global risk-off / tightening liquidity | 🟢 proxy via UUP ETF (Polygon) or 🟡 FRED `DTWEXBGS` |
| **Copper/Gold ratio** | growth (copper) vs fear (gold); rising = risk-on/reflation | 🟢 via CPER/GLD ETFs (Polygon) |
| **Real yields / 10Y** | discount-rate pressure on growth/duration | 🟡 FRED |

**Buildability:** 🟡 mostly — a tiny **free daily FRED pull** (HY spread, 2s10s, broad dollar)
plus 🟢 ETF proxies (UUP, CPER, GLD) we can already pull from Polygon. HY spread direction +
2s10s sign + dollar trend give a clean macro risk-on/off overlay. **No paid data.**
Sources: [FRED HY OAS (BAMLH0A0HYM2)](https://fred.stlouisfed.org/series/BAMLH0A0HYM2),
[FRED 2s10s (T10Y2Y)](https://fred.stlouisfed.org/series/T10Y2Y),
[credit spreads chart (LongtermTrends)](https://www.longtermtrends.com/bond-yield-credit-spreads/),
[2s10s vs S&P (MacroMicro)](https://en.macromicro.me/collections/34/us-stock-relative/398/us-10-2-yield-curve-gspc).

## 4. Intraday internals — "real-time buying/selling pressure"
The day-trader's tape: instantaneous breadth/volume across the exchange.

| Indicator | What it tells you | Build |
|--|--|--|
| **NYSE $TICK** | net up-ticking vs down-ticking stocks *right now*; extremes (±1000) = thrust/exhaustion | 🔴 true exchange feed / 🟡 universe proxy |
| **TRIN (Arms)** = (adv/dec)/(upVol/downVol) | breadth vs volume; >3 oversold, <0.5 overbought; rising = weak | 🔴 true / 🟡 proxy |
| **$ADD** (advancers − decliners) | intraday net breadth | 🟡 proxy |
| **$UVOL/$DVOL** (up/down volume) | conviction behind the move | 🟡 proxy |

**Buildability:** the *true* exchange-calculated $TICK/$TRIN are **not** in our free Polygon
data (🔴). BUT we have **5-minute bars for the whole 400-name universe**, so we can compute
**proxies** at 5m granularity: net advancers−decliners ($ADD-proxy), up/down volume ratio
(TRIN-proxy), and a per-bar "tick-proxy" (names up vs down on the bar). 🟡 — not identical to
NYSE-wide internals but directionally useful and **buildable from data in hand**. Caveat: a
400-name proxy is coarser than the ~3000-stock NYSE feed; flag as approximate.
Sources: [Arms Index/TRIN (Fidelity)](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/trin),
[TRIN (StockCharts)](https://chartschool.stockcharts.com/table-of-contents/market-indicators/arms-index-trin),
[Market internals TICK/ADD/VOLD/TRIN (TradingView)](https://www.tradingview.com/script/aNrCpO4C-Market-Internals-TICK-ADD-VOLD-TRIN-VIX/).

## 5. Positioning & sentiment — "is everyone already on one side?" (contrarian)
Sentiment extremes are *contrarian*: max bullishness near tops, max fear near bottoms.

| Indicator | What it tells you | Build |
|--|--|--|
| **AAII Bull/Bear** (weekly) | retail sentiment; >45% bulls = froth, extreme bears = bottoms | 🟡 free weekly download |
| **NAAIM Exposure** (weekly) | active managers' equity exposure; >120% = overheating, low = panic | 🟡 free weekly |
| **CFTC COT** (weekly) | spec/hedge-fund net futures positioning (proxy for crowding) | 🟡 free weekly |
| **CNN Fear & Greed** (0–100) | 7-indicator composite; <20 extreme fear (buy), >80 greed (caution) | 🟡 free (overlaps our own internals) |
| **Put/Call extremes** | spikes = fear (contrarian bullish) | 🟡 CBOE free |

**Buildability:** 🟡 — all are **free weekly downloads** (AAII, NAAIM, CFTC) or public (CNN F&G).
Note F&G is itself a *composite* of breadth/vol/put-call/junk-bond demand — so it overlaps
the score we'd build; useful as a cross-check, not an independent input. Best contrarian use:
flag *extremes* (both tails), not day-to-day wiggles.
Sources: [AAII Sentiment Survey](https://www.aaii.com/sentimentsurvey),
[NAAIM Exposure Index](https://www.naaim.org/programs/naaim-exposure-index/),
[CNN Fear & Greed](https://www.cnn.com/markets/fear-and-greed),
[Sentiment as a contrarian indicator (AAII)](https://www.aaii.com/journal/article/feature-investor-sentiment-as-a-contrarian-indicator).

---

## 6. Buildability summary & Phase-2 recommendation

### Buildability across all categories
| Category | Headline indicators | Verdict |
|--|--|--|
| **Breadth** | %>50/200-MA, McClellan Osc/Summation, A/D line, new-highs−lows, up/down vol | **🟢 build now** — Polygon grouped-daily + our universe, NO new data |
| **Volatility** | VIX level, **term-structure ratio (VIX9D/VIX3M)**, put/call, VVIX | 🟡 small free FRED/CBOE pull |
| **Credit/macro** | **HY spread (BAMLH0A0HYM2)**, 2s10s (T10Y2Y), dollar, copper/gold | 🟡 free FRED + 🟢 ETF proxies (Polygon) |
| **Intraday** | $TICK, TRIN, $ADD, up/down vol | 🔴 true exchange feed · 🟡 proxy from our 5m universe |
| **Positioning** | AAII, NAAIM, COT, F&G, put/call extremes | 🟡 free weekly downloads |

**Bottom line:** a genuinely useful daily market-direction overlay is buildable **right now**
with zero paid data — breadth is fully in hand, and vol/credit are tiny free pulls.

### Recommended Phase-2 build: a daily "TAPE SCORE" (0–100 risk-on/off)
*(Recommendation only — needs your approval; the screener stays frozen.)*

Combine the buildable internals into one 0–100 gauge (higher = risk-on):
- **Breadth (≈50% weight, 🟢):** % of universe above 200-MA, McClellan Summation sign/slope,
  A/D-line slope, net new-highs−lows. (computed daily from data we already pull)
- **Volatility (≈25%, 🟡):** VIX term-structure ratio — contango = risk-on, **backwardation
  (VIX9D>VIX3M) = risk-off**; plus VIX level percentile.
- **Credit/macro (≈25%, 🟡):** HY-spread direction (widening = risk-off) + 2s10s sign.
- *(Optional contrarian overlay, 🟡 weekly:* AAII/NAAIM/F&G *extremes* flag froth/capitulation.)*

**How it would gate/size screener setups (proposed):**
- **Tape ≥ 60 (risk-on):** take longs at full size; shorts down-weighted.
- **Tape 40–60 (neutral):** normal/reduced size; rely on per-name confluence + catalyst.
- **Tape < 40 (risk-off):** suppress/penalize new longs, favor shorts, tighten — i.e. the
  overlay would *dim* long setups when the broad tape is deteriorating (the missing context
  the user asked for). This pairs naturally with the catalyst filter (opposed-news) already
  shown to help.

**Delivery options (all behind approval):** (a) a `market.tape_score` module + a small panel/row
in the screener header; (b) a daily ntfy "tape" line; (c) a backtest first — does gating squeeze
setups by tape score actually improve win-rate? (the disciplined path, like the catalyst test).

---

## Status log
- **Iter 1 (2026-06-27):** scaffolded doc; researched & wrote §1 Breadth (🟢 buildable from
  Polygon grouped-daily + our universe — no new data) and §2 Volatility (🟡 free FRED/CBOE pull).
- **Iter 2 (2026-06-27):** §3 Credit & macro (🟡 free FRED pull + 🟢 ETF proxies UUP/CPER/GLD) and
  §4 Intraday internals (true $TICK/$TRIN 🔴 not in free data, but 🟡 proxies from our 5m universe).
- **Iter 3 / final (2026-06-27):** §5 Positioning & sentiment (🟡 free weekly: AAII/NAAIM/COT/F&G)
  and §6 buildability summary + Phase-2 **tape-score** recommendation. Research pass COMPLETE.

## BUILD log (Phase 2 — approved: display-only, full scope, mock UI first; screener frozen)
- **Build iter 1 (2026-06-27):** `reporting/tape_score.py` — Tape Score 0-100 from breadth
  (our universe, ffill-aligned: %>50/200MA, McClellan, A/D slope, new-hi-lo) + volatility
  (free FRED VIXCLS/VXVCLS term ratio + VIX percentile) + credit (free FRED HY spread +
  2s10s). Weights 0.5/0.25/0.25. Live now: 54.5 NEUTRAL. Exposed at `GET /api/tape` (cached
  5m, additive — screener matrix untouched). NEXT: UI/UX research + mockups (Claude Design),
  user picks integration style before final UI build.
- **Build iter 2 (2026-06-27):** user picked hybrid tape bar (TapeBar/TapeDetail) — DONE & live.
- **Build iter 3 (2026-06-27):** approved internals page + intraday proxy + DECISION engine (both
  badge & panel, full synthesis). Backend done: `reporting/intraday_internals.py` (5m universe
  proxy: TICK/$ADD/TRIN/pct_up; `/api/internals/intraday`), `reporting/decision.py` (rules-based
  verdict GO/CAUTION/AVOID 0-100 = tape + setup tier/confluence + news aligned/opposed + intraday;
  `/api/decisions` batch, `/api/decision?symbol` deep). Live: 25 GO / 28 AVOID / 2 CAUTION.
  NEXT: tape_history endpoint + frontend (internals page w/ sparklines + intraday chart + decision
  panel; screener verdict-badge column; nav). Display-only, matrix untouched.

---

## Status log
- **Iter 1 (2026-06-27):** scaffolded doc; researched & wrote §1 Breadth (🟢 buildable from
  Polygon grouped-daily + our universe — no new data) and §2 Volatility (🟡 free FRED/CBOE pull).
- **Iter 2 (2026-06-27):** §3 Credit & macro (🟡 free FRED pull + 🟢 ETF proxies UUP/CPER/GLD) and
  §4 Intraday internals (true $TICK/$TRIN 🔴 not in free data, but 🟡 proxies from our 5m universe).
  Next: §5 positioning & sentiment.

## Trade-expression recommender + vehicle backtest (2026-06-27)
`reporting/trade_plan.py` (+ `/api/trade_plan`, `/internals` decision panel, `/plan?symbol` view)
recommends HOW to enter — STOCK / LONG / DEBIT / CREDIT / CALENDAR / IRON_CONDOR — from live-chain
IV/HV regime + liquidity gate + earnings + horizon, with R/R + entry/exit.
Backtest verdict (validation/vehicle_backtest.py, 149 setups, real ORATS frictions):
STOCK 56.4% win / -0.4% (most reliable); NAKED_LONG 39.6% win / +3.3% on premium (convex lottery);
DEBIT -17% on risk (loses, per P8). => trust the engine's STOCK bias; options only selectively.
Display-only; not auto-trading.

## GEX (gamma exposure) overlay (2026-06-28)
reporting/gex.py — net GEX = Σcall(gamma·OI) − Σput(gamma·OI) ×100·spot²·0.01 over nearest 3
expiries (Tradier chain). Sign = regime: POSITIVE → dealers dampen → pinning/mean-revert
(squeeze may stall); NEGATIVE → dealers amplify → trend/breakout (squeeze tailwind). Call wall
(max call gamma·OI)=resistance/target; put wall=support; crude flip level. /api/gex?symbol.
Integrated into the VERDICT (reporting/decision.py): neg-gamma +8 conviction, pos-gamma −8,
wall-just-ahead −6. /api/decision now includes gex + GEX-adjusted verdict (EW 79→73, pos-gamma).
GEX is per-ticker deep overlay (NOT the 55-ticker batch — ~3 chain fetches each, cached 15m).
CAVEAT: dealer-sign is the standard ASSUMPTION; per-name noisier than index; not backtestable
(no historical OI in ORATS cache) — live overlay. Sources: SpotGamma, perfiliev.com GEX calc.
