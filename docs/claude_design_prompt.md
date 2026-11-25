# Claude Design prompt — Squeeze Screener UI/UX redesign

Paste the block below into the Claude Design tool. It describes every screen built so far
so the tool can propose a cohesive redesign. (Reference inventory: Screener `/`, TapeBar +
TapeDetail, ChartModal, Internals `/internals`, Trade Plan `/plan?symbol`, shared Sparkline
+ verdict/news/confluence badges.)

---

```
You are helping me redesign the UI/UX of a personal quant trading web app
("Squeeze Screener") — a Next.js 14 + React + TypeScript SPA, local-only, dark theme,
data-dense, dependency-light (charts are hand-drawn SVG, no chart libs). One user: a
solo discretionary swing/options trader who needs to scan setups and decide FAST.
Current UI is functional but utilitarian (self-rated 5/10). I want a cohesive, modern,
information-dense-but-scannable trader aesthetic (think Bloomberg/TradingView clarity).
Keep it dark, keep it fast, keep charts as lightweight SVG (no heavy libs).

Please propose a redesign — layout, visual hierarchy, component design, color system,
typography, spacing, navigation, and interaction patterns — for these screens:

1) SCREENER (home) — a "confluence matrix": rows = ~55 tickers, columns = 12 timeframes
   (5m,15m,30m,1h,2h,3h,4h,1d,2d,3d,4d,1w). Each cell is a squeeze setup: tier
   (●ALERT / ○WATCH) + direction (▲up/▼down), color-coded green/red, empty = no setup.
   Extra columns: Verdict (GO/CAUTION/AVOID badge), Confluence (score+bar, how many
   timeframes agree), News (count + sentiment dot). Controls: text search, ALERT/WATCH
   toggle, UP/DOWN toggle, min-confluence slider, "hide-opposed" toggle. A legend.
   Clicking a cell opens a chart. Goal: instantly spot the highest-conviction,
   multi-timeframe, catalyst-aligned setups. It's a big grid — help it stay scannable.

2) TAPE BAR (persistent strip atop the screener) — left: a market "Tape Score" gauge
   (0–100 risk-on/off, color-zoned, label) ; right: a sliding ticker-tape of market
   internals (%>200MA, McClellan, VIX, VIX term ratio, HY credit spread, 2s10s, etc.),
   each color-coded. Clicking opens a detail modal (breadth / volatility / credit cards).

3) CHART MODAL — candlestick + 20-MA line + squeeze-state dots (red=tight…green=fired)
   + a momentum histogram strip, plus a recent-news headlines list with sentiment dots.

4) INTERNALS PAGE — a big Tape-Score gauge + plain-English read; 120-day sparkline
   history cards for Tape/Breadth/Volatility/Credit; an intraday breadth panel
   (TICK-proxy bars + %up / advancers-decliners / TRIN); and a DECISION PANEL: pick a
   ticker → a GO/CAUTION/AVOID verdict with conviction (0–100) + bullet reasons + the
   supporting layers (tape, setup, news, intraday) + a "How to enter" recommendation.

5) TRADE PLAN PAGE (?symbol=) — for a chosen ticker, the recommended way to ENTER:
   a vehicle (Stock / Long option / Debit spread / Credit spread / Calendar / Iron
   Condor) with the structure, entry/target/stop, R/R, max-loss, breakeven; the inputs
   (IV, HV, IV/HV, term ratio, expected move, DTE, earnings); and a ranked ALTERNATIVES
   table comparing all vehicles (cost / max-loss / max-profit / R/R).

Cross-cutting asks:
- A consistent design system: color tokens (the green/red/amber risk semantics matter),
  typography scale, spacing, badges/chips, tables, cards, gauges, sparklines, modals.
- Strong information hierarchy and "decisiveness" — make the GO/CAUTION/AVOID and the
  high-confluence setups pop; de-emphasize noise.
- Clean navigation between Screener ⇄ Internals ⇄ Trade Plan.
- Density vs readability for the 55×12 matrix (heatmap treatment, sticky headers, hover).
- Light/responsive enough to run locally; suggest if/where a chart lib is worth it.
- Deliver: layout wireframes/mockups per screen, a component inventory, a color/type
  system, and concrete before/after suggestions I can implement in Next/React/Tailwind
  (or plain CSS — currently plain CSS variables).
```

---

Optional add-ons depending on what Claude Design supports:
- "Output as a Tailwind-based component set."
- "Generate an interactive HTML mockup of the screener matrix and the internals page."

Current stack note for implementation later: plain CSS variables (dark theme tokens in
`webapp/frontend/app/globals.css`); components under `webapp/frontend/app/components/`.
