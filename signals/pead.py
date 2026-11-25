"""Post-Earnings-Announcement Drift (PEAD) — an event-driven, non-price-pattern signal.

PEAD is a well-documented anomaly: after an earnings surprise, prices keep drifting in
the surprise direction for weeks. We don't have estimates, so we use the EARNINGS-DAY
REACTION as the surprise proxy: near each quarterly filing date, the announcement day
is the volume-spike day; its return's sign is the drift direction.

This is genuinely different from the price-pattern signals — and largely idiosyncratic
(earnings are stock-specific), so it's a candidate for real market-agnostic alpha.
"""

from __future__ import annotations

import pandas as pd


def detect_events(df: pd.DataFrame, filing_dates: list[str], win_back: int = 45,
                  vol_z_min: float = 2.0):
    """Return [(event_date, reaction_return)] — the announcement day near each filing
    date (highest volume vs its 20d average) and that day's signed return."""
    volratio = df["volume"] / df["volume"].rolling(20).mean()
    ret = df["close"].pct_change()
    events = []
    for fd in filing_dates:
        fd = pd.Timestamp(fd, tz="UTC")
        w = df.index[(df.index >= fd - pd.Timedelta(days=win_back)) &
                     (df.index <= fd + pd.Timedelta(days=2))]
        sub = volratio.reindex(w).dropna()
        if len(sub) < 5:
            continue
        t = sub.idxmax()
        if sub[t] < vol_z_min:        # no clear volume spike -> skip
            continue
        r = ret.get(t)
        if r is None or r != r:
            continue
        events.append((t, float(r)))
    return events
