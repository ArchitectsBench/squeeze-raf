"""Polygon.io adapter for 2h/4h/1d OHLCV (full-universe scale; D8).

Uses the Aggregates (bars) endpoint with `adjusted=true` so prices are
split-adjusted at the source (T7). Daily is fetched natively; 2h/4h are built
from native **30-minute** bars (whose :00/:30 ET grid includes a clean 09:30
open) via the session-anchored resampler. The 30-min base is fetched once per
symbol and memoized, so 2h and 4h share a single network pull. Gaps are
surfaced, never filled (R7). Pure ingestion — no indicator/signal logic.

Rate limit: Polygon's free tier is 5 req/min with 2y history and 5k bars/page.
The limiter defaults to 5/min; override via `POLYGON_CALLS_PER_MIN` (set it
higher on a paid plan). Key: env var `POLYGON_API_KEY` (or repo-root .env).
Docs: https://polygon.io/docs/stocks/get_v2_aggs_ticker__stocksticker__range
"""

from __future__ import annotations

import os

import httpx
import pandas as pd

from .base import RateLimiter, http_get_json, require_key
from .resample import to_timeframe
from .schema import normalize_ohlcv

BASE_URL = "https://api.polygon.io"

# (multiplier, timespan) of the bars we request from Polygon per target tf.
# Intraday uses 30-minute bars: Polygon's grid is :00/:30 ET, so it includes a
# clean 09:30 open bar. (Its 1-hour bars are anchored to 09:00 and would fold
# the 09:30-10:00 open into a pre-market bucket that RTH filtering discards.)
_NATIVE = {
    "1d": (1, "day"),
    "2h": (30, "minute"),  # fetch 30min, resample to 2h
    "4h": (30, "minute"),  # fetch 30min, resample to 4h
    "30min": (30, "minute"),  # native 30-min RTH bars
    "15min": (15, "minute"),  # native 15-min RTH bars
    "5min": (5, "minute"),    # native 5-min RTH bars
    "1h": (30, "minute"),     # fetch 30min, resample to 1h (09:30-anchored)
    "3h": (30, "minute"),     # fetch 30min, resample to 3h (09:30-anchored)
    "2d": (1, "day"),         # fetch daily, resample to 2 trading days
    "3d": (1, "day"),         # 3 trading days
    "4d": (1, "day"),         # 4 trading days
    "1w": (1, "day"),         # weekly (ending Friday)
}

# Polygon caps a single page at 50k (paid) but free tier returns 5k; either way
# pagination via next_url handles the rest.
_PAGE_LIMIT = 50000


class PolygonAdapter:
    name = "polygon"

    def __init__(self, calls_per_min: float | None = None, timeout: float = 30.0):
        self.api_key = require_key("POLYGON_API_KEY", "Polygon.io")
        if calls_per_min is None:
            calls_per_min = float(os.environ.get("POLYGON_CALLS_PER_MIN", "5"))
        self.limiter = RateLimiter(calls_per_min)
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)
        self._base_cache: dict[tuple, pd.DataFrame] = {}

    def __del__(self):  # best-effort cleanup for script use
        try:
            self._client.close()
        except Exception:
            pass

    def _agg(self, symbol, mult, timespan, start, end) -> pd.DataFrame:
        """Page through the aggregates endpoint for one symbol/granularity.
        Memoized per (symbol, mult, timespan, start, end) so 2h and 4h reuse the
        same 30-minute pull instead of fetching it twice."""
        key = (symbol.upper(), mult, timespan, start, end)
        if key in self._base_cache:
            return self._base_cache[key]

        url = (
            f"{BASE_URL}/v2/aggs/ticker/{symbol.upper()}/range/"
            f"{mult}/{timespan}/{start}/{end}"
        )
        params: dict | None = {
            "adjusted": "true",
            "sort": "asc",
            "limit": _PAGE_LIMIT,
            "apiKey": self.api_key,
        }
        rows: list[dict] = []
        next_url: str | None = url
        while next_url:
            self.limiter.wait()
            data = http_get_json(
                self._client, next_url, params or {"apiKey": self.api_key}
            )
            rows.extend(data.get("results", []) or [])
            next_url = data.get("next_url")
            params = None  # next_url already carries query params except the key

        if not rows:
            df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        else:
            df = pd.DataFrame(rows)
            # t = ms epoch (UTC) at bar start; o/h/l/c/v are OHLCV.
            df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
            df = df.rename(
                columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
            )
            df = normalize_ohlcv(df)

        self._base_cache[key] = df
        return df

    def fetch(self, symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        if timeframe not in _NATIVE:
            raise ValueError(f"unsupported timeframe {timeframe!r}")
        mult, timespan = _NATIVE[timeframe]
        base = self._agg(symbol, mult, timespan, start, end)
        base_freq = "1d" if timespan == "day" else "30min"
        return to_timeframe(base, timeframe, base_freq=base_freq)
