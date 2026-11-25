"""Derive extra timeframes LOCALLY from already-cached bases (no API calls).

    uv run python -m data.derive_timeframes

1h/3h are resampled from the cached 30min bars (09:30-anchored); 2d/3d/4d/1w from the
cached 1d bars. Coverage of the derived frame matches its base (30min ~recent window,
1d full history). Re-run after any base refresh to keep them in sync.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from data.cache import ParquetCache
from data.resample import resample_intraday, resample_multiday

UNIV = Path(__file__).resolve().parent.parent / "config" / "liquid_universe.json"
FROM_30M = ["1h", "2h", "3h", "4h"]
FROM_1D = ["2d", "3d", "4d", "1w"]


def main(argv=None) -> int:
    syms = json.loads(UNIV.read_text())
    cache = ParquetCache()
    made = {tf: 0 for tf in FROM_30M + FROM_1D}
    for s in syms:
        b30 = cache.read(s, "30min")
        if b30 is not None and len(b30):
            for tf in FROM_30M:
                try:
                    out = resample_intraday(b30, tf)
                    if len(out):
                        cache.write(s, tf, out, vendor="derived")
                        made[tf] += 1
                except Exception:
                    pass
        b1d = cache.read(s, "1d")
        if b1d is not None and len(b1d):
            for tf in FROM_1D:
                try:
                    out = resample_multiday(b1d, tf)
                    if len(out):
                        cache.write(s, tf, out, vendor="derived")
                        made[tf] += 1
                except Exception:
                    pass
    print("derived timeframes:", ", ".join(f"{tf}={n}" for tf, n in made.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
