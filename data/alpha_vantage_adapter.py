"""Alpha Vantage adapter for 2h/4h/1d OHLCV (15-symbol pilot only; D8).

Daily uses TIME_SERIES_DAILY_ADJUSTED and back-applies the split coefficient so
OHLC is **split-adjusted** (T7) — we deliberately do NOT dividend-adjust, to keep
the price signal on as-traded levels. Intraday uses TIME_SERIES_INTRADAY at
60-min resolution (`adjusted=true`, RTH only), pulled in rate-limited monthly
slices and stitched (overlaps de-duped, timestamps kept monotonic — R7), then
resampled to 2h/4h.

Prototyping/pilot use only — the monthly-slice cadence is painful at 500-name
scale; use Polygon for the full pull.

Key: env var `ALPHAVANTAGE_API_KEY` (or repo-root .env).
Note: intraday extended history + DAILY_ADJUSTED are premium endpoints on AV.
"""

from __future__ import annotations

import httpx
import pandas as pd

from .base import RateLimiter, http_get_json, require_key
from .resample import to_timeframe
from .schema import normalize_ohlcv

BASE_URL = "https://www.alphavantage.co/query"


def _months(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM slices spanning [start, end]."""
    rng = pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M")
    return [p.strftime("%Y-%m") for p in rng]


def _check_payload(data: dict) -> None:
    """AV signals errors/limits in the body with a 200 status."""
    for key in ("Error Message", "Note", "Information"):
        if key in data:
            raise RuntimeError(f"Alpha Vantage: {data[key]}")


class AlphaVantageAdapter:
    name = "alpha_vantage"

    def __init__(self, calls_per_min: float = 5.0, timeout: float = 60.0):
        self.api_key = require_key("ALPHAVANTAGE_API_KEY", "Alpha Vantage")
        self.limiter = RateLimiter(calls_per_min)
        self.timeout = timeout

    # --- daily -----------------------------------------------------------
    def _daily_adjusted(self, client, symbol) -> pd.DataFrame:
        self.limiter.wait()
        data = http_get_json(
            client,
            BASE_URL,
            {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol.upper(),
                "outputsize": "full",
                "apikey": self.api_key,
            },
        )
        _check_payload(data)
        ts = data["Time Series (Daily)"]
        df = pd.DataFrame(ts).T
        df.index = pd.to_datetime(df.index)
        df = df.astype(float).sort_index()
        # Split-adjust: divide historical prices by the product of all FUTURE
        # split coefficients; scale volume up by the same factor.
        s = df["8. split coefficient"]
        future_split = s[::-1].cumprod().shift(1).fillna(1.0)[::-1]
        out = pd.DataFrame(
            {
                "open": df["1. open"] / future_split,
                "high": df["2. high"] / future_split,
                "low": df["3. low"] / future_split,
                "close": df["4. close"] / future_split,
                "volume": df["6. volume"] * future_split,
            }
        )
        out.index = out.index.tz_localize("UTC")
        return normalize_ohlcv(out)

    # --- intraday --------------------------------------------------------
    def _intraday_month(self, client, symbol, month) -> pd.DataFrame:
        self.limiter.wait()
        data = http_get_json(
            client,
            BASE_URL,
            {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": symbol.upper(),
                "interval": "30min",
                "month": month,
                "outputsize": "full",
                "adjusted": "true",
                "extended_hours": "false",
                "apikey": self.api_key,
            },
        )
        _check_payload(data)
        # NOTE: AV's intraday timestamp convention (interval start vs end) is
        # UNVERIFIED here (no key in this env). The Polygon path is the validated
        # one; before trusting AV bars, check the 09:30 open bar against a ToS
        # fixture in P2 (indicator fidelity).
        ts = data.get("Time Series (30min)")
        if not ts:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(ts).T.astype(float)
        df.index = pd.to_datetime(df.index)
        df = df.rename(
            columns={
                "1. open": "open",
                "2. high": "high",
                "3. low": "low",
                "4. close": "close",
                "5. volume": "volume",
            }
        )
        # AV intraday timestamps are US/Eastern, tz-naive -> localize then UTC.
        df.index = df.index.tz_localize("America/New_York", nonexistent="shift_forward",
                                        ambiguous="NaT")
        df = df[df.index.notna()]
        df.index = df.index.tz_convert("UTC")
        return normalize_ohlcv(df)

    def _intraday_hourly(self, client, symbol, start, end) -> pd.DataFrame:
        frames = [
            self._intraday_month(client, symbol, m) for m in _months(start, end)
        ]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        merged = pd.concat(frames)
        merged = normalize_ohlcv(merged)  # sorts + de-dups overlaps
        return merged.loc[
            (merged.index >= pd.Timestamp(start, tz="UTC"))
            & (merged.index <= pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))
        ]

    # --- public ----------------------------------------------------------
    def fetch(
        self, symbol: str, timeframe: str, start: str, end: str
    ) -> pd.DataFrame:
        with httpx.Client(timeout=self.timeout) as client:
            if timeframe == "1d":
                daily = self._daily_adjusted(client, symbol)
                daily = daily.loc[
                    (daily.index >= pd.Timestamp(start, tz="UTC"))
                    & (daily.index <= pd.Timestamp(end, tz="UTC"))
                ]
                return to_timeframe(daily, "1d", base_freq="1d")
            if timeframe in ("2h", "4h"):
                hourly = self._intraday_hourly(client, symbol, start, end)
                return to_timeframe(hourly, timeframe, base_freq="1h")
        raise ValueError(f"unsupported timeframe {timeframe!r}")
