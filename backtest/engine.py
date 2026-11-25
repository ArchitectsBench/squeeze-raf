"""Event backtest engine (BUILD_SPEC.md §5 Stage 2; Phase P4).

Hard guarantees (CLAUDE.md rules 1, 2; T5):
- Signal on close[t] -> fill at **open[t+1]**. The simulator never uses a bar's own
  close to fill an entry on that same bar. Intrabar stop/target fills use only the
  held bar's own high/low (legitimate — we are in the position through that bar).
- Costs ALWAYS on (per-side slippage+commission from config), charged at entry and
  exit. There is no frictionless path.
- Long AND short, symmetric. Vol-target sizing (each trade risks a fixed % at its
  ATR stop).

The per-symbol simulator returns a per-bar net return stream; the portfolio is the
equal-weight average across symbols (vol-target makes per-trade risk comparable, so
equal weighting ≈ equal risk). Metrics are computed on the portfolio stream.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indicators.ta import atr as atr_wilder
from indicators.squeeze import HIST_DOWN, HIST_UP
from signals import Signals

from .costs import per_side_fraction
from .sizing import vol_target_fraction

BARS_PER_YEAR = {"2h": 1008, "4h": 504, "1d": 252}
DEFAULT_TIME_STOP = {"2h": 8, "4h": 6, "1d": 5}  # horizon-dependent holding cap


@dataclass
class ExitParams:
    time_stop_bars: int = 8
    atr_stop_mult: float = 1.5
    profit_target_R: float = 1.5
    use_momentum_exit: bool = True
    use_profit_target: bool = True   # off -> let the time-stop capture the edge horizon


@dataclass
class SizingParams:
    risk_pct: float = 0.005        # 0.5% risk per trade (config sizing.risk_per_trade_pct)
    max_leverage: float = 1.0


def _momentum_exit_signals(sig: Signals) -> tuple[pd.Series, pd.Series]:
    """Exit a long when momentum turns clearly bearish or an opposite entry prints
    (evaluated at close -> executed next open). Mirror for short."""
    hist_state = sig.sq.hist_state
    long_exit = (hist_state == HIST_DOWN) | sig.short_entry
    short_exit = (hist_state == HIST_UP) | sig.long_entry
    return long_exit.fillna(False), short_exit.fillna(False)


def simulate_symbol(
    df: pd.DataFrame,
    sig: Signals,
    cost_class: str,
    exits: ExitParams,
    sizing: SizingParams,
) -> tuple[np.ndarray, list[dict]]:
    """Run one symbol. Returns (per-bar net return array aligned to df, trades)."""
    n = len(df)
    open_ = df["open"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    atr14 = atr_wilder(df, 14).to_numpy()

    long_entry = sig.long_entry.to_numpy()
    short_entry = sig.short_entry.to_numpy()
    long_exit_sig, short_exit_sig = _momentum_exit_signals(sig)
    long_exit_sig = long_exit_sig.to_numpy()
    short_exit_sig = short_exit_sig.to_numpy()

    side_cost = per_side_fraction(cost_class)
    r = np.zeros(n)
    trades: list[dict] = []

    pos = 0
    entry_price = stop = target = exposure = 0.0
    entry_bar = time_exit_bar = -1
    trade_pnl = 0.0

    # Each bar is accounted EXACTLY ONCE as (mark_out/mark_in - 1): mark_in is the
    # entry price on the entry bar, else the prior close; mark_out is the exit
    # price if exiting this bar, else this bar's close. No undo/double-count.
    for t in range(1, n):
        if pos == 0:
            # entry signalled at close[t-1] -> fill at open[t]
            direction = 1 if long_entry[t - 1] else (-1 if short_entry[t - 1] else 0)
            if direction == 0:
                continue
            px = open_[t]
            a = atr14[t - 1]
            if not (np.isfinite(a) and a > 0 and np.isfinite(px) and px > 0):
                continue
            exposure = vol_target_fraction(px, a, sizing.risk_pct, exits.atr_stop_mult,
                                           sizing.max_leverage)
            if exposure <= 0:
                continue
            stop_dist = exits.atr_stop_mult * a
            pos, entry_price, entry_bar = direction, px, t
            stop = px - direction * stop_dist
            target = px + direction * exits.profit_target_R * stop_dist
            time_exit_bar = t + exits.time_stop_bars
            trade_pnl = 0.0
            # fall through: bar t is also a holding bar (may even stop out intrabar)

        mark_in = entry_price if t == entry_bar else close[t - 1]
        exiting = False
        mark_out = close[t]

        # 1) signal/time exits (known by close[t-1] or duration) fill at open[t]
        if t > entry_bar:
            time_due = t >= time_exit_bar
            mom_due = exits.use_momentum_exit and (
                (pos == 1 and long_exit_sig[t - 1]) or (pos == -1 and short_exit_sig[t - 1])
            )
            if time_due or mom_due:
                mark_out, exiting = open_[t], True

        # 2) intrabar stop (always) / target (only if enabled) on bar t
        if not exiting:
            tgt = exits.use_profit_target
            if pos == 1:
                if low[t] <= stop:
                    mark_out, exiting = min(open_[t], stop), True
                elif tgt and high[t] >= target:
                    mark_out, exiting = max(open_[t], target), True
            else:
                if high[t] >= stop:
                    mark_out, exiting = max(open_[t], stop), True
                elif tgt and low[t] <= target:
                    mark_out, exiting = min(open_[t], target), True

        if not exiting and t == n - 1:  # force-close at the final bar's close
            exiting = True

        entry_cost = side_cost if t == entry_bar else 0.0
        exit_cost = side_cost if exiting else 0.0
        bar_ret = pos * (mark_out / mark_in - 1.0)
        r[t] = exposure * bar_ret - (entry_cost + exit_cost) * exposure
        trade_pnl += bar_ret - (entry_cost + exit_cost)

        if exiting:
            trades.append({"dir": pos, "pnl": trade_pnl,
                           "entry_bar": entry_bar, "exit_bar": t,
                           "entry_price": entry_price, "exit_price": mark_out,
                           "exposure": exposure})
            pos = 0

    return r, trades


def metrics(returns: pd.Series, trades: pd.DataFrame, ann: int) -> dict:
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return {"sharpe": 0.0, "sortino": 0.0, "cagr": 0.0, "max_dd": 0.0,
                "calmar": 0.0, "exposure": 0.0, "n_trades": len(trades),
                "win_rate": np.nan, "profit_factor": np.nan, "avg_win": np.nan,
                "avg_loss": np.nan, "turnover_per_yr": 0.0}
    mean, sd = r.mean(), r.std()
    downside = r[r < 0].std()
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    n = len(r)
    cagr = eq.iloc[-1] ** (ann / n) - 1 if eq.iloc[-1] > 0 else -1.0
    sharpe = mean / sd * np.sqrt(ann)
    sortino = mean / downside * np.sqrt(ann) if downside and downside > 0 else np.nan

    pnl = trades["pnl"] if len(trades) else pd.Series(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.nan
    return {
        "sharpe": float(sharpe),
        "sortino": float(sortino) if sortino == sortino else np.nan,
        "cagr": float(cagr),
        "max_dd": float(dd),
        "calmar": float(cagr / abs(dd)) if dd < 0 else np.nan,
        "exposure": float((returns != 0).mean()),
        "n_trades": int(len(trades)),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else np.nan,
        "profit_factor": float(pf) if pf == pf else np.nan,
        "avg_win": float(wins.mean()) if len(wins) else np.nan,
        "avg_loss": float(losses.mean()) if len(losses) else np.nan,
        "turnover_per_yr": float(len(trades) / (n / ann)) if n else 0.0,
    }


def run_backtest(
    frames: dict[str, pd.DataFrame],
    signals_by_symbol: dict[str, Signals],
    timeframe: str,
    cost_class_by_symbol: dict[str, str],
    exits: ExitParams | None = None,
    sizing: SizingParams | None = None,
) -> tuple[pd.Series, pd.DataFrame, dict]:
    """Backtest one mode/timeframe across the universe. Returns (portfolio per-bar
    returns, trades table, metrics)."""
    exits = exits or ExitParams(time_stop_bars=DEFAULT_TIME_STOP.get(timeframe, 8))
    sizing = sizing or SizingParams()
    ann = BARS_PER_YEAR.get(timeframe, 252)

    per_symbol_r: dict[str, pd.Series] = {}
    all_trades = []
    for sym, df in frames.items():
        sig = signals_by_symbol[sym]
        r, trades = simulate_symbol(df, sig, cost_class_by_symbol.get(sym, "equities"),
                                    exits, sizing)
        per_symbol_r[sym] = pd.Series(r, index=df.index)
        for tr in trades:
            tr["symbol"] = sym
        all_trades.extend(trades)

    mat = pd.DataFrame(per_symbol_r).sort_index()
    portfolio = mat.mean(axis=1).fillna(0.0)  # equal-weight across symbols
    trades_df = pd.DataFrame(all_trades)
    return portfolio, trades_df, metrics(portfolio, trades_df, ann)
