"""Load the trading universe + cost classes from config/universe.yaml.

Keeps the pilot vs full split, and tags each symbol with a cost class
(equity / liquid ETF / high-vol ETF) so downstream layers apply the right
slippage (config `costs`). The data layer itself only needs the symbol list and
which adapter/slippage class each belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"


@dataclass(frozen=True)
class Symbol:
    ticker: str
    cost_class: str  # "equities" | "etfs_liquid" | "etfs_high_vol"


@dataclass(frozen=True)
class Universe:
    symbols: tuple[Symbol, ...]
    timeframes: tuple[str, ...]

    @property
    def tickers(self) -> list[str]:
        return [s.ticker for s in self.symbols]

    def cost_class(self, ticker: str) -> str:
        for s in self.symbols:
            if s.ticker == ticker.upper():
                return s.cost_class
        raise KeyError(ticker)


def _load_yaml(path: Path | str = CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_universe(name: str = "pilot", path: Path | str = CONFIG_PATH) -> Universe:
    """Load a named universe (`pilot`, `broad`, ...) from config.

    `etfs_liquid` is tagged as `equities` for cost purposes (tight spreads);
    `etfs_high_vol` keeps its own wider-slippage class (R9 / T7).
    """
    cfg = _load_yaml(path)
    try:
        block = cfg["universe"][name]
    except KeyError:
        raise KeyError(f"universe {name!r} not in config (have {list(cfg['universe'])})")
    class_map = {
        "equities": block.get("equities", []),
        "etfs_liquid": block.get("etfs_liquid", []),
        "etfs_high_vol": block.get("etfs_high_vol", []),
    }
    symbols: list[Symbol] = []
    seen: set[str] = set()
    for cost_class, tickers in class_map.items():
        for t in tickers:
            t = t.upper()
            if t in seen:
                continue
            seen.add(t)
            symbols.append(Symbol(ticker=t, cost_class=cost_class))

    timeframes = tuple(cfg["timeframes"]["signal_bars"])
    return Universe(symbols=tuple(symbols), timeframes=timeframes)


def load_pilot(path: Path | str = CONFIG_PATH) -> Universe:
    """Backwards-compatible alias for the pilot universe."""
    return load_universe("pilot", path)


def load_pairs_pool(path: Path | str = CONFIG_PATH) -> dict[str, list[str]]:
    """Sector -> [tickers] for the pairs pool (candidate pairs formed within sector)."""
    cfg = _load_yaml(path)
    return {sec: [t.upper() for t in names]
            for sec, names in cfg["universe"]["pairs_pool"].items()}


def pairs_pool_tickers(path: Path | str = CONFIG_PATH) -> list[str]:
    seen: list[str] = []
    for names in load_pairs_pool(path).values():
        for t in names:
            if t not in seen:
                seen.append(t)
    return seen
