"""Trade-decision synthesizer — turns the layers into a decisive GO / CAUTION / AVOID.

Transparent, rules-based (no black box, tunable). For a setup it combines:
  - TAPE (market direction): does the broad tape support this side?
  - SETUP strength: ALERT vs WATCH + multi-timeframe confluence
  - CATALYST: news aligned (+) or opposed (−, backtest-confirmed loser)
  - INTRADAY breadth: is the tape confirming right now?
Returns a 0-100 conviction, a verdict, and the reasons (so you can be decisive AND know why).

GATING/SORTING enabled 2026-07-02 (user-approved after validation/verdict_backtest.py
passed: GO 55.7% win/+105bps vs AVOID 45.1%/-40bps, t~4.7, 2465 setups): the screener
sorts by verdict+conviction and default-hides AVOID + opposed-news; squeeze_scan does
not push AVOID-verdict alerts to ntfy. Caveat stands: strong first read, ~23mo/one
regime, not walk-forward — NOT a graduated auto-trade signal.
"""

from __future__ import annotations

import numpy as np

GO, CAUTION, AVOID = "GO", "CAUTION", "AVOID"


def verdict(direction: str, tier: str | None, conf_n: int, conf_score: float,
            tape_score: float, news_sentiment: str | None,
            intraday_score: float | None, gex: dict | None = None) -> dict:
    """direction: 'UP'/'DOWN'; tier: 'ALERT'/'WATCH'/None; sentiment: positive/negative/neutral.
    gex (optional): {available, regime_sign 'POS'/'NEG', call_wall, put_wall,
    call_wall_dist_pct, put_wall_dist_pct} — dealer-gamma overlay."""
    up = direction == "UP"
    reasons: list[str] = []
    score = 50.0

    # 1) Tape supports the side? (risk-on favors longs, risk-off favors shorts)
    tape_support = (tape_score - 50) if up else (50 - tape_score)
    score += tape_support * 0.6
    if abs(tape_support) >= 10:
        reasons.append(f"Tape {tape_score:.0f} {'supports' if tape_support > 0 else 'fights'} "
                       f"{'longs' if up else 'shorts'}")

    # 2) Setup strength — ALERT bonus reduced 15 -> 4 (parity with WATCH) per the
    # pre-registered tier_weight_test 2026-07-04: +15 promoted weak ALERT setups into
    # GO (GO mean -7bps); at +4 GO precision 56.3% and mean +106bps. The tier label
    # remains displayed; it just no longer outranks the tape.
    if tier == "ALERT":
        score += 4
        reasons.append("ALERT (trend+momentum confirm)")
    elif tier == "WATCH":
        score += 4
        reasons.append("WATCH (coil, momentum pending)")
    # NOTE: multi-TF confluence contributes NOTHING — validation/confluence_backtest.py
    # FAILED its pre-registered gate 2026-07-02 (conf>=1 was -2.0 win-pts vs conf=0).
    # conf_n/conf_score stay as params for API compat but are display-only.
    _ = (conf_n, conf_score)

    # 3) Catalyst (news) — opposed is a strong, backtest-confirmed negative
    if news_sentiment and news_sentiment != "neutral":
        aligned = (news_sentiment == "positive") == up
        if aligned:
            score += 10
            reasons.append("news aligned ✓")
        else:
            score -= 25
            reasons.append("news OPPOSED ⚠ (backtest: loses)")

    # 4) Intraday breadth confirmation (today's tape)
    if intraday_score is not None:
        intra_support = (intraday_score - 50) if up else (50 - intraday_score)
        score += intra_support * 0.2
        if abs(intra_support) >= 10:
            reasons.append(f"intraday breadth {'confirms' if intra_support > 0 else 'against'}")

    # 5) Dealer-gamma (GEX): REMOVED from scoring 2026-07-04 — validation/
    # gex_squeeze_backtest.py FAILED both pre-registered gates (1,908 setups, real
    # historical OI): the NEG-gamma "+8 tailwind" was BACKWARDS (NEG 47.5% win vs POS
    # 53.3% @10d), and wall-proximity missed its bar (+4.0 pts, p=0.128). Walls/flip
    # remain DISPLAY LEVELS in the UI and trade_plan; they no longer move conviction.
    _ = gex

    score = float(np.clip(score, 0, 100))
    call = GO if score >= 65 else AVOID if score < 45 else CAUTION
    return {"call": call, "conviction": round(score, 0), "direction": direction, "reasons": reasons}
