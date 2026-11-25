"""Data layer: vendor adapters + parquet cache for the underlying OHLCV universe.

Contract (see BUILD_SPEC.md §8, §9; Phase P1):
- Provide 2h / 4h / 1d OHLCV bars for the pilot universe (config/universe.yaml).
- Split-adjust (T7); decay-aware handling is the caller's concern downstream.
- All bars cached as parquet (pyarrow); cache dir is NOT committed to git.
- A data-integrity check (no gaps/dupes, monotonic timestamps) gates P1.
"""

from .cache import ParquetCache
from .ingest import IngestResult, get_adapter, ingest_universe
from .schema import IntegrityReport, check_integrity, normalize_ohlcv
from .universe import Universe, load_pilot, load_universe

__all__ = [
    "ParquetCache",
    "IngestResult",
    "get_adapter",
    "ingest_universe",
    "IntegrityReport",
    "check_integrity",
    "normalize_ohlcv",
    "Universe",
    "load_pilot",
    "load_universe",
]
