# ThinkOrSwim fidelity fixtures (Stage 0 gate)

Drop ToS exports here as CSV — one file per symbol/timeframe — and
`tests/test_indicator_fidelity.py` will validate the Python ports against them
automatically. Until then those tests SKIP (the rest of the suite stays green).

## Filename
`<SYMBOL>_<TIMEFRAME>.csv`, e.g. `AAPL_1d.csv`, `SPY_4h.csv`, `NVDA_2h.csv`.
Timeframe suffix is one of `2h`, `4h`, `1d` (sets the squeeze `Length`: 13 for
2h/4h, 21 for 1d).

## Required columns
- a timestamp column: one of `timestamp`, `datetime`, `time`, `date`
  (any format `pandas.to_datetime` accepts; exported in market time is fine —
  it is converted to UTC, so make sure ToS exports an unambiguous timestamp).
- OHLCV: `open`, `high`, `low`, `close`, `volume`.

## Comparison columns (include any you can export — only present ones are checked)
| CSV column      | ToS study output                          | tolerance      |
|-----------------|-------------------------------------------|----------------|
| `squeeze_state` | 3/2/1/0 compression dots (tight→none)     | exact          |
| `squeeze_hist`  | TTM momentum histogram value              | 2% relative    |
| `ema8`          | EMA(close, 8)                             | 2% relative    |
| `ema13`         | EMA(close, 13)                            | 2% relative    |
| `ema21`         | EMA(close, 21)                            | 2% relative    |
| `raf_sc`        | RAF Setup Count (Ready/Aim)               | exact          |
| `raf_major`     | RAF Fisher "Major"/Fire line              | 2% relative    |
| `raf_savgstate` | RAF displaced-TEMA regime (+1/0/-1)       | exact          |

First 60 bars are ignored (indicator warmup / EMA seeding transient).

## Notes on conventions to confirm with the first export
A few ToS conventions are assumed in the port and will be locked the moment a
fixture disagrees (see indicators/ docstrings):
- EMA seeding = recursive from the first bar (`ExpAverage`/`compoundValue`).
- `StDev` = population (ddof=0).
- ATR = Wilder's smoothing; squeeze KC width = EMA-of-TrueRange.
- Stochastics = EXPONENTIAL averaging, normalized `(%K-50)/50`.
If a column is off by a constant factor or a one-bar lag, that pins which
convention to switch — open an issue/decision before "fixing" the math.
