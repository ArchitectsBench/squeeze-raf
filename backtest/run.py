"""P4 backtest runner.

    uv run python -m backtest.run --universe broad --mode A

Runs the (edge-check-graduated) confluence mode across 2h/4h/1d on the universe,
prints the full metrics table, and writes an equity/drawdown tearsheet PNG to
tearsheets/ (gitignored). Costs always on; next-bar-open execution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from data.cache import ParquetCache  # noqa: E402
from data.universe import load_universe  # noqa: E402
from signals import ConfluenceParams, generate  # noqa: E402

from .engine import BARS_PER_YEAR, run_backtest  # noqa: E402

TEARSHEET_DIR = Path(__file__).resolve().parent.parent / "tearsheets"


def _build_signals(frames, tf, mode):
    params = ConfluenceParams.for_timeframe(tf)
    return {s: generate(df, params, mode=mode) for s, df in frames.items()}


def tearsheet(returns: pd.Series, title: str, path: Path):
    eq = (1 + returns).cumprod()
    dd = eq / eq.cummax() - 1
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(eq.index, eq.values, lw=1.2)
    ax1.set_title(title)
    ax1.set_ylabel("equity (x)")
    ax1.grid(alpha=0.3)
    ax2.fill_between(dd.index, dd.values, 0, color="crimson", alpha=0.4)
    ax2.set_ylabel("drawdown")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    ap = argparse.ArgumentParser(description="P4 backtest")
    ap.add_argument("--universe", default="broad")
    ap.add_argument("--mode", default="A", choices=["A", "B", "C"])
    args = ap.parse_args(argv)

    universe = load_universe(args.universe)
    cost_class = {s.ticker: s.cost_class for s in universe.symbols}
    cache = ParquetCache()

    print(f"=== P4 BACKTEST — mode {args.mode}, universe '{args.universe}' ===")
    print("next-bar-open exec | costs always on | vol-target sizing | long+short\n")

    rows = []
    equity_curves = {}
    for tf in universe.timeframes:
        frames = {s: cache.read(s, tf) for s in universe.tickers if cache.exists(s, tf)}
        frames = {s: df for s, df in frames.items() if df is not None and len(df) > 100}
        if not frames:
            continue
        sigs = _build_signals(frames, tf, args.mode)
        port, trades, m = run_backtest(frames, sigs, tf, cost_class)
        equity_curves[tf] = (1 + port).cumprod()
        m_row = {"timeframe": tf, "mode": args.mode, **m}
        rows.append(m_row)
        if tf == "2h":  # the graduated timeframe
            tearsheet(port, f"Confluence {args.mode} · {tf} · {args.universe}",
                      TEARSHEET_DIR / f"backtest_{args.mode}_{tf}_{args.universe}.png")

    table = pd.DataFrame(rows)
    cols = ["timeframe", "mode", "sharpe", "sortino", "cagr", "max_dd", "calmar",
            "exposure", "n_trades", "win_rate", "profit_factor", "turnover_per_yr"]
    show = table[cols].copy()
    for c in ["sharpe", "sortino", "calmar", "profit_factor", "turnover_per_yr"]:
        show[c] = show[c].round(2)
    for c in ["cagr", "max_dd", "exposure", "win_rate"]:
        show[c] = (show[c] * 100).round(1)
    print(show.to_string(index=False))
    print("\n(cagr/max_dd/exposure/win_rate in %; ann. factor "
          f"{ {tf: BARS_PER_YEAR[tf] for tf in universe.timeframes} })")
    print(f"tearsheet -> {TEARSHEET_DIR}/backtest_{args.mode}_2h_{args.universe}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
