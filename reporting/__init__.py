"""Reporting: tearsheets + the multi-TF recommendation CLI (Phase P9).

Contract (BUILD_SPEC.md §4, §9):
- Multi-TF scan with the 4h+1d alignment bonus -> ranked spread recommendations
  (CSV + tearsheet), tagged by timeframe and holding bucket.
- `--as-of` reproduces recommendations for a fixed historical date, deterministically.
- `--paper` dry-runs Tradier-sandbox orders for forward testing.

Import submodules directly (e.g. `from reporting.recommend import scan`) to avoid a
double-import warning under `python -m reporting.recommend`.
"""
