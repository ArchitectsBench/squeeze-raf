"""ORATS EOD options adapter (BUILD_SPEC.md §8; default options vendor, D9).

Reads ORATS SMV Strikes daily zips — preferring a LOCAL ARCHIVE (ORATS_ARCHIVE_DIR in
.env, e.g. a synced Google Drive folder with {YYYY}/ORATS_SMV_Strikes_{YYYYMMDD}.zip,
full history back to 2007) and falling back to S3 (needs ORATS_AWS_* creds; key was
invalidated 2026-06-30). On read we FILTER to the universe tickers and cache a small
parquet per day under data/orats/ (gitignored), so the multi-GB market history never
lands in the repo and re-runs are instant.

Schema (per strike row): ticker, stkPx/spot_px, expirDate (M/D/YYYY), yte, strike,
cBidPx/cValue/cAskPx, pBidPx/pValue/pAskPx, cMidIv/pMidIv, delta (CALL delta; put
delta = delta-1), gamma/theta/vega, iRate, divRate.

Credentials come from .env (ORATS_AWS_* / ORATS_S3_BUCKET). ROTATE the key after use.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pandas as pd

from data.base import load_dotenv

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "orats"
_KEEP = ["ticker", "stkPx", "expirDate", "yte", "strike",
         "cBidPx", "cValue", "cAskPx", "pBidPx", "pValue", "pAskPx",
         "cMidIv", "pMidIv", "delta", "gamma", "theta", "vega",
         # open interest + volume (for historical GEX / liquidity). Spellings vary by ORATS
         # product; absent ones are skipped (see load_day's leniency). Phase 0 confirms.
         "cOpInt", "pOpInt", "cVolu", "pVolu",
         "cOi", "pOi", "callOpenInterest", "putOpenInterest"]

# columns whose PRESENCE marks a parquet as the new (OI-bearing) schema. A cached file lacking
# all of these was built by the old ingest and is re-fetched from S3 on next read.
_OI_COLS = ("cOpInt", "pOpInt", "cOi", "pOi", "callOpenInterest", "putOpenInterest")


class OratsAdapter:
    def __init__(self, universe: set[str] | None = None):
        load_dotenv()
        self.bucket = os.environ.get("ORATS_S3_BUCKET")   # optional when archive present
        self.region = os.environ.get("ORATS_AWS_REGION", "us-east-1")
        self.archive = os.environ.get("ORATS_ARCHIVE_DIR")
        self.universe = {u.upper() for u in universe} if universe else None
        self._client = None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def s3(self):
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "s3", region_name=self.region,
                aws_access_key_id=os.environ["ORATS_AWS_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["ORATS_AWS_SECRET_ACCESS_KEY"])
        return self._client

    def _cache_path(self, date: pd.Timestamp) -> Path:
        return CACHE_DIR / f"{date:%Y%m%d}.parquet"

    def _s3_key(self, date: pd.Timestamp) -> str:
        return f"{date:%Y}/ORATS_SMV_Strikes_{date:%Y%m%d}.zip"

    def _raw_zip(self, date: pd.Timestamp) -> bytes | None:
        """Daily zip bytes: local archive first (no creds needed), S3 fallback."""
        if self.archive:
            p = Path(self.archive) / f"{date:%Y}" / f"ORATS_SMV_Strikes_{date:%Y%m%d}.zip"
            if p.is_file():
                return p.read_bytes()
        if not self.bucket:
            return None
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=self._s3_key(date))["Body"].read()
        except Exception:
            return None

    def load_day(self, date) -> pd.DataFrame | None:
        """Universe-filtered chain for one trading day (cached). None if no file
        (weekend/holiday) or empty after filtering."""
        date = pd.Timestamp(date).normalize()
        path = self._cache_path(date)
        cached = None
        if path.is_file():
            cached = pd.read_parquet(path)
            # Re-fetch only if this is an old (no-OI) cache and the day actually had data. Empty
            # frames (weekend/holiday) stay cached — re-fetching them would be pointless.
            stale_schema = len(cached) and not any(c in cached.columns for c in _OI_COLS)
            if not stale_schema:
                return cached if len(cached) else None
        raw = self._raw_zip(date)
        if raw is None:
            # No archive file / no valid creds. Fall back to the old (no-OI) cache if we
            # have it, so existing price-only backtests keep working.
            return cached if (cached is not None and len(cached)) else None
        z = zipfile.ZipFile(io.BytesIO(raw))
        keep = set(_KEEP)
        df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])),
                         usecols=lambda c: c in keep or c == "ticker")
        if self.universe is not None:
            df = df[df["ticker"].isin(self.universe)]
        df = df[[c for c in _KEEP if c in df.columns]].copy()
        df["expiry"] = pd.to_datetime(df["expirDate"], format="%m/%d/%Y")
        df.to_parquet(path)              # cache (empty frame caches too -> avoids re-fetch)
        return df if len(df) else None

    def chain(self, ticker: str, date) -> pd.DataFrame | None:
        day = self.load_day(date)
        if day is None:
            return None
        c = day[day["ticker"] == ticker.upper()]
        return c if len(c) else None

    def prefetch(self, dates) -> int:
        """Download+cache a set of dates up front. Returns count with data."""
        n = 0
        for d in sorted(set(pd.Timestamp(x).normalize() for x in dates)):
            if self.load_day(d) is not None:
                n += 1
        return n
