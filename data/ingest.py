"""Orchestrate a universe pull: adapter -> integrity check -> parquet cache.

`ingest_universe` walks (symbol x timeframe), fetches split-adjusted bars, runs
`check_integrity`, and only writes frames that pass (failures are reported, never
silently cached). The config hash for each write captures vendor + window so the
cache is reproducible (CLAUDE.md rule 7).
"""

from __future__ import annotations

from dataclasses import dataclass

from .alpha_vantage_adapter import AlphaVantageAdapter
from .base import Adapter
from .cache import ParquetCache
from .polygon_adapter import PolygonAdapter
from .schema import IntegrityReport, check_integrity
from .universe import Universe, load_pilot

_ADAPTERS = {"polygon": PolygonAdapter, "alpha_vantage": AlphaVantageAdapter}


def get_adapter(vendor: str) -> Adapter:
    try:
        return _ADAPTERS[vendor]()
    except KeyError:
        raise ValueError(
            f"unknown vendor {vendor!r}; choose from {sorted(_ADAPTERS)}"
        )


@dataclass
class IngestResult:
    symbol: str
    timeframe: str
    rows: int
    written: bool
    report: IntegrityReport | None
    error: str | None = None


def ingest_universe(
    vendor: str,
    start: str,
    end: str,
    *,
    universe: Universe | None = None,
    cache: ParquetCache | None = None,
    timeframes: tuple[str, ...] | None = None,
    force: bool = False,
) -> list[IngestResult]:
    universe = universe or load_pilot()
    cache = cache or ParquetCache()
    timeframes = timeframes or universe.timeframes
    adapter = get_adapter(vendor)

    results: list[IngestResult] = []
    for ticker in universe.tickers:
        for tf in timeframes:
            if not force and cache.exists(ticker, tf):
                df = cache.read(ticker, tf)
                rep = check_integrity(df, ticker, tf)
                results.append(IngestResult(ticker, tf, len(df), False, rep))
                continue
            try:
                df = adapter.fetch(ticker, tf, start, end)
            except Exception as exc:  # surface, keep going
                results.append(IngestResult(ticker, tf, 0, False, None, str(exc)))
                continue
            rep = check_integrity(df, ticker, tf)
            written = False
            if rep.ok and not df.empty:
                cfg = {
                    "vendor": vendor,
                    "symbol": ticker,
                    "timeframe": tf,
                    "start": start,
                    "end": end,
                }
                cache.write(ticker, tf, df, vendor=vendor, cfg=cfg)
                written = True
            results.append(IngestResult(ticker, tf, len(df), written, rep))
    return results
