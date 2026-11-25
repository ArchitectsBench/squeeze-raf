"""DTE selection from holding-horizon bucket (BUILD_SPEC.md §4; universe.yaml holding_to_dte).

    1d -> 0-2 | 2d -> 2-5 | 3d -> 5-10 | 5d -> 7-21

The holding bucket is tagged by the signal timeframe. A representative DTE (for the
BS expected-move / pricing math) is the band midpoint, floored at 1 (a 0-DTE has no
time value to model).
"""

from __future__ import annotations

from pathlib import Path

import yaml

UNIVERSE = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"

# signal timeframe -> holding bucket key (§4)
TF_TO_BUCKET = {"2h": "1d", "4h": "2d", "1d": "5d"}


def _map(path=UNIVERSE) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)["holding_to_dte"]


def dte_band(bucket: str, path=UNIVERSE) -> tuple[int, int]:
    lo, hi = _map(path)[bucket]
    return int(lo), int(hi)


def representative_dte(bucket: str, path=UNIVERSE) -> int:
    lo, hi = dte_band(bucket, path)
    return max(1, round((lo + hi) / 2))


def bucket_for_timeframe(tf: str) -> str:
    return TF_TO_BUCKET.get(tf, "5d")
