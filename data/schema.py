"""OHLCV data contract + integrity checks (the gate for Phase P1).

This module is the single source of truth for what a clean bar frame looks like.
Adapters normalize into this shape, the cache stores it, and the data-integrity
test (tests/test_data_integrity.py) asserts against `check_integrity`.

Contract for a bar DataFrame:
- index: tz-aware UTC `DatetimeIndex` named "timestamp", strictly increasing, unique.
- columns: exactly [open, high, low, close, volume], float64.
- no NaN in any bar; OHLC internally consistent; prices > 0; volume >= 0.

Timeframes are the canonical strings in `TIMEFRAMES`. Bars are *split-adjusted*
at ingestion (vendor `adjusted=true` / *_ADJUSTED endpoints), so no look-ahead
adjustment happens here — this layer only validates and stores.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Canonical schema -----------------------------------------------------------
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
INDEX_NAME = "timestamp"
# Full timeframe ladder (2026-06-26 expansion). Latent bug found 2026-07-06: the
# derived TFs were never registered here, so every 15min/1h/3h/2d/3d/4d/1w frame
# failed integrity as "unknown timeframe" — invisible while the old daily script
# swallowed non-zero exits, fatal once refresh failures became blocking.
TIMEFRAMES = ("5min", "15min", "30min", "1h", "2h", "3h", "4h",
              "1d", "2d", "3d", "4d", "1w")
MULTIDAY = ("2d", "3d", "4d", "1w")

# Per-timeframe expected bar spacing (within a single trading session for
# intraday; one trading day for daily). Used by the gap checker.
TIMEFRAME_DELTA = {
    "5min": pd.Timedelta(minutes=5),
    "15min": pd.Timedelta(minutes=15),
    "30min": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "2h": pd.Timedelta(hours=2),
    "3h": pd.Timedelta(hours=3),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}

# Multiday bars span N trading days, so their NORMAL calendar spacing exceeds the
# daily 5-day rule (a weekly bar is 7 days apart by construction). Per-TF ceilings:
# nominal spacing + weekend/holiday allowance; beyond this = a genuinely missing bar.
MULTIDAY_MAX_GAP = {
    "2d": pd.Timedelta(days=8),
    "3d": pd.Timedelta(days=9),
    "4d": pd.Timedelta(days=11),
    "1w": pd.Timedelta(days=10),
}

# Gap violations OLDER than this are permanent historical artifacts (ticker
# changes like FB->META or BK->BNY, late IPOs) — reported as WARNINGS, not
# blocking failures. Judged by when the gap ENDED: if data has been continuous
# for this long, today's signals are unaffected. What gates trading is RECENT data.
HISTORIC_AFTER = pd.Timedelta(days=30)

# Sub-30min bars legitimately skip windows where a name simply didn't trade
# (Polygon omits empty bars). Tolerate up to this many consecutive missing bars
# within a session on fast TFs; larger holes still flag.
FAST_TF_MISSING_BARS_OK = 2
FAST_TFS = ("5min", "15min")

# Daily bars legitimately skip weekends + market holidays. Anything longer than
# this between consecutive daily bars is a real hole (a missing week), not a
# holiday long-weekend (longest US market closures are ~4 calendar days).
MAX_DAILY_GAP = pd.Timedelta(days=5)


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a raw adapter frame into the canonical contract.

    - lowercases columns, keeps only OHLCV
    - parses the index to tz-aware UTC, names it `timestamp`
    - sorts, drops exact-duplicate timestamps (keep last = freshest fetch)
    - casts to float64

    Does NOT fabricate or fill missing bars — gaps are surfaced, never patched
    (CLAUDE.md / R7). Call `check_integrity` afterwards to validate.
    """
    df = df.rename(columns={c: c.lower() for c in df.columns})
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"frame missing required columns: {missing}")
    df = df[OHLCV_COLUMNS].copy()

    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df.index = idx
    df.index.name = INDEX_NAME

    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    return df.astype({c: "float64" for c in OHLCV_COLUMNS})


@dataclass
class IntegrityReport:
    """Result of `check_integrity`. `ok` is True only when violations is empty.
    `warnings` holds HISTORIC artifacts (old gaps from ticker changes/IPOs) that
    are reported but never block — recent-data integrity is what gates trading."""

    symbol: str
    timeframe: str
    rows: int
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def __str__(self) -> str:
        head = f"{self.symbol} {self.timeframe} ({self.rows} rows): "
        if self.ok:
            return head + "OK"
        return head + "FAIL\n  - " + "\n  - ".join(self.violations)


def _session_gap_violations(index: pd.DatetimeIndex, timeframe: str) -> list[str]:
    """Intraday: each trading day must be a contiguous run on the bar grid.

    We do NOT require every calendar day to be present (holidays/half-days are
    fine) — we require that *within* each day that has bars, there are no interior
    holes. This is exactly the Alpha-Vantage "silent hole" failure mode (R7).
    """
    step = TIMEFRAME_DELTA[timeframe]
    # fast TFs: a couple of consecutive no-trade windows are normal market
    # microstructure for mid-caps, not data corruption
    tol = step * (FAST_TF_MISSING_BARS_OK + 1) if timeframe in FAST_TFS else step
    out: list[str] = []
    # group by ET calendar date so sessions align to the local trading day
    et = index.tz_convert("America/New_York")
    for day, positions in pd.Series(range(len(index)), index=et).groupby(et.date):
        if len(positions) < 2:
            continue
        day_idx = index[positions.to_numpy()]
        diffs = day_idx.to_series().diff().dropna()
        bad = diffs[diffs > tol]
        for ts, gap in bad.items():
            out.append(
                f"intraday gap on {day}: {gap} between bars ending {ts} "
                f"(expected {step}, tolerance {tol})"
            )
    return out


def _split_by_age(items: list[tuple[pd.Timestamp, str]]) -> tuple[list[str], list[str]]:
    """(recent_violations, historic_warnings) — old artifacts don't block trading."""
    cutoff = pd.Timestamp.now(tz="UTC") - HISTORIC_AFTER
    recent = [msg for ts, msg in items if ts >= cutoff]
    historic = [f"historic: {msg}" for ts, msg in items if ts < cutoff]
    return recent, historic


def _daily_gap_violations(index: pd.DatetimeIndex,
                          max_gap: pd.Timedelta = MAX_DAILY_GAP) -> list[tuple[pd.Timestamp, str]]:
    diffs = index.to_series().diff().dropna()
    out: list[tuple[pd.Timestamp, str]] = []
    for ts, gap in diffs[diffs > max_gap].items():
        out.append((ts, f"daily gap of {gap} before {ts.date()} (> {max_gap})"))
    return out


def _intraday_block_gap_violations(index: pd.DatetimeIndex) -> list[str]:
    """Intraday: flag missing multi-session BLOCKS (e.g. a vanished week/month).

    The per-session check only catches holes *within* a day; this catches whole
    missing stretches — like the ~3-month gap a ticker change leaves behind. A
    normal overnight/weekend gap is < MAX_DAILY_GAP, so that threshold cleanly
    separates "market closed" from "data missing"."""
    diffs = index.to_series().diff().dropna()
    out: list[str] = []
    for ts, gap in diffs[diffs > MAX_DAILY_GAP].items():
        out.append(f"intraday block gap of {gap} before {ts} (> {MAX_DAILY_GAP})")
    return out


def check_integrity(df: pd.DataFrame, symbol: str, timeframe: str) -> IntegrityReport:
    """Validate a bar frame against the contract. Never raises — collects all
    violations into the report so the coverage run can show every problem."""
    rep = IntegrityReport(symbol=symbol, timeframe=timeframe, rows=len(df))
    v = rep.violations

    if timeframe not in TIMEFRAMES:
        v.append(f"unknown timeframe {timeframe!r}")
        return rep
    if df.empty:
        v.append("empty frame (no bars)")
        return rep

    # --- structural ---
    if list(df.columns) != OHLCV_COLUMNS:
        v.append(f"columns {list(df.columns)} != {OHLCV_COLUMNS}")
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        v.append("index is not a DatetimeIndex")
        return rep
    if idx.tz is None:
        v.append("index is tz-naive (must be UTC)")
    elif str(idx.tz) != "UTC":
        v.append(f"index tz is {idx.tz} (must be UTC)")
    if idx.name != INDEX_NAME:
        v.append(f"index name {idx.name!r} != {INDEX_NAME!r}")

    # --- duplicates / monotonic ---
    n_dupe = int(idx.duplicated().sum())
    if n_dupe:
        v.append(f"{n_dupe} duplicate timestamp(s)")
    if not idx.is_monotonic_increasing:
        v.append("index is not monotonically increasing")

    # --- NaN ---
    n_nan = int(df.isna().sum().sum())
    if n_nan:
        cols = df.columns[df.isna().any()].tolist()
        v.append(f"{n_nan} NaN value(s) in columns {cols}")

    # --- OHLC sanity (only meaningful if no NaN) ---
    if not n_nan:
        o, h, lo, c, vol = (df[x] for x in OHLCV_COLUMNS)
        if (df[["open", "high", "low", "close"]] <= 0).any().any():
            v.append("non-positive price(s) present")
        if (vol < 0).any():
            v.append("negative volume present")
        hi_ok = (h >= o) & (h >= c) & (h >= lo)
        lo_ok = (lo <= o) & (lo <= c) & (lo <= h)
        n_bad = int((~(hi_ok & lo_ok)).sum())
        if n_bad:
            v.append(f"{n_bad} bar(s) violate high>=O,C,L and low<=O,C,H")

    # --- gaps (needs sorted unique index to be meaningful) ---
    if not n_dupe and idx.is_monotonic_increasing:
        if timeframe == "1d":
            recent, historic = _split_by_age(_daily_gap_violations(idx))
            v.extend(recent)
            rep.warnings.extend(historic)
        elif timeframe in MULTIDAY:
            recent, historic = _split_by_age(
                _daily_gap_violations(idx, max_gap=MULTIDAY_MAX_GAP[timeframe]))
            v.extend(recent)
            rep.warnings.extend(historic)
        else:
            v.extend(_session_gap_violations(idx, timeframe))
            v.extend(_intraday_block_gap_violations(idx))

    return rep
