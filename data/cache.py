"""Parquet cache for OHLCV bars (pyarrow).

Layout (under a root that is gitignored — see .gitignore `data/`):

    data/cache/<SYMBOL>/<timeframe>.parquet

Each file is one symbol/timeframe frame in the canonical schema (see
`data.schema`). Writes are atomic (temp file + rename) so a crashed run never
leaves a half-written parquet. Provenance — vendor, fetch window, a config hash
(CLAUDE.md rule 7) and the row count — is stored in the parquet's file-level
key/value metadata, so the cache is self-describing without a sidecar DB.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .schema import OHLCV_COLUMNS, normalize_ohlcv

DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent / "cache"
_META_KEY = b"squeeze_raf_meta"


def config_hash(payload: dict) -> str:
    """Deterministic short hash of a fetch's config — logged per write so any
    cached frame can be traced back to exactly how it was produced."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class CacheEntry:
    symbol: str
    timeframe: str
    path: Path
    rows: int
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    vendor: str | None
    cfg_hash: str | None


class ParquetCache:
    def __init__(self, root: Path | str = DEFAULT_CACHE_ROOT):
        self.root = Path(root)

    def path_for(self, symbol: str, timeframe: str) -> Path:
        return self.root / symbol.upper() / f"{timeframe}.parquet"

    def exists(self, symbol: str, timeframe: str) -> bool:
        return self.path_for(symbol, timeframe).is_file()

    def read(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        """Return the cached frame, or None on cache miss. Re-normalizes on read
        so callers always get the canonical schema regardless of how it landed
        on disk."""
        path = self.path_for(symbol, timeframe)
        if not path.is_file():
            return None
        df = pd.read_parquet(path)
        return normalize_ohlcv(df)

    def read_meta(self, symbol: str, timeframe: str) -> dict | None:
        path = self.path_for(symbol, timeframe)
        if not path.is_file():
            return None
        meta = pq.read_metadata(path).metadata or {}
        raw = meta.get(_META_KEY)
        return json.loads(raw) if raw else {}

    def write(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        *,
        vendor: str | None = None,
        cfg: dict | None = None,
    ) -> Path:
        """Atomically write a normalized frame. Returns the file path. Embeds
        provenance metadata (vendor, range, row count, config hash)."""
        df = normalize_ohlcv(df)
        path = self.path_for(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "vendor": vendor,
            "rows": int(len(df)),
            "start": str(df.index.min()) if len(df) else None,
            "end": str(df.index.max()) if len(df) else None,
            "cfg_hash": config_hash(cfg) if cfg else None,
        }
        table = pa.Table.from_pandas(df[OHLCV_COLUMNS], preserve_index=True)
        existing = table.schema.metadata or {}
        table = table.replace_schema_metadata(
            {**existing, _META_KEY: json.dumps(meta).encode()}
        )

        tmp = path.with_suffix(f".parquet.tmp-{os.getpid()}")
        pq.write_table(table, tmp)
        os.replace(tmp, path)  # atomic on POSIX
        return path

    def entries(self) -> list[CacheEntry]:
        """Scan the cache root and summarize every stored frame (for the
        coverage report)."""
        out: list[CacheEntry] = []
        if not self.root.is_dir():
            return out
        for path in sorted(self.root.glob("*/*.parquet")):
            symbol = path.parent.name
            timeframe = path.stem
            meta = self.read_meta(symbol, timeframe) or {}
            start = pd.Timestamp(meta["start"]) if meta.get("start") else None
            end = pd.Timestamp(meta["end"]) if meta.get("end") else None
            out.append(
                CacheEntry(
                    symbol=symbol,
                    timeframe=timeframe,
                    path=path,
                    rows=int(meta.get("rows", 0)),
                    start=start,
                    end=end,
                    vendor=meta.get("vendor"),
                    cfg_hash=meta.get("cfg_hash"),
                )
            )
        return out
