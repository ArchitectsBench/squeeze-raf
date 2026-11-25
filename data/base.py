"""Shared adapter plumbing: env/key loading, a rate-limited HTTP client, and the
adapter Protocol every vendor implements.

Adapters are pure ingestion — they return split-adjusted OHLCV in the canonical
schema and nothing else (no indicators, no signals). They surface gaps rather
than filling them (R7).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Protocol

import httpx
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | str = _PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader (no dependency). Lines `KEY=VALUE`; does not override
    already-set environment variables. Secrets stay out of git (.gitignore)."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        os.environ.setdefault(key, val)


def require_key(env_var: str, vendor: str) -> str:
    load_dotenv()
    key = os.environ.get(env_var)
    if not key:
        raise RuntimeError(
            f"{vendor} API key not found. Set {env_var} in the environment or in "
            f"a .env file at the repo root."
        )
    return key


class RateLimiter:
    """Simple minimum-interval throttle (calls/min). Good enough for the pilot
    pull; the full-universe run on Polygon should use its own concurrency."""

    def __init__(self, calls_per_min: float):
        self.min_interval = 60.0 / calls_per_min if calls_per_min > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


class Adapter(Protocol):
    name: str

    def fetch(
        self, symbol: str, timeframe: str, start: str, end: str
    ) -> pd.DataFrame:
        """Return split-adjusted OHLCV bars (canonical schema) for
        [start, end]. `timeframe` in {2h, 4h, 1d}."""
        ...


def http_get_json(
    client: httpx.Client, url: str, params: dict, *, retries: int = 6
) -> dict:
    """GET with backoff on 429/5xx. Honors a `Retry-After` header when present
    (Polygon's free-tier 429s want ~a full minute). Raises on persistent
    failure."""
    delay = 5.0
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.get(url, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                if attempt < retries - 1:
                    time.sleep(min(wait, 65.0))
                    delay = min(delay * 2, 60.0)
                raise httpx.HTTPStatusError(
                    f"retryable status {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # ValueError = bad JSON
            last_exc = exc
            if attempt == retries - 1:
                break
            if not isinstance(exc, httpx.HTTPStatusError):
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_exc}")
