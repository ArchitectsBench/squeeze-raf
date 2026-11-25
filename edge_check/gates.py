"""Pre-registered edge-check gate evaluator (BUILD_SPEC.md §5; config/gates.yaml).

Thresholds are READ-ONLY here — they are loaded from config/gates.yaml and never
mutated to make a rule pass (R8 goal-hacking). The verdict is computed at the
PRIMARY horizon pre-registered in event_study.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from .event_study import PRIMARY_HORIZON_BARS

GATES_PATH = Path(__file__).resolve().parent.parent / "config" / "gates.yaml"
UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"


def load_edge_gate(path: Path | str = GATES_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)["edge_check"]


def round_trip_cost_bps(cost_class: str = "equities",
                        path: Path | str = UNIVERSE_PATH) -> float:
    """Round-trip cost in bps: slippage on entry + exit (commission is per-share
    $0 for equities here). Conservative — used for the 'beats cost' criterion."""
    with open(path) as f:
        costs = yaml.safe_load(f)["costs"]
    slip = costs.get(cost_class, costs["equities"])["slippage_bps"]
    return 2.0 * float(slip)  # entry + exit


@dataclass
class GateVerdict:
    mode: str
    timeframe: str
    horizon: int
    passed: bool
    criteria: dict       # name -> (value, threshold, ok)

    def __str__(self) -> str:
        head = f"[{'PASS' if self.passed else 'FAIL'}] mode {self.mode} {self.timeframe} @h={self.horizon}"
        lines = [head]
        for name, (val, thr, ok) in self.criteria.items():
            mark = "ok " if ok else "XX "
            lines.append(f"    {mark}{name}: {val} (need {thr})")
        return "\n".join(lines)


def evaluate(
    summary: pd.DataFrame,
    mono: dict,
    mode: str,
    timeframe: str,
    gate: dict | None = None,
    cost_class: str = "equities",
) -> GateVerdict:
    """Evaluate the pre-registered edge gate for one (mode, timeframe) at the
    primary horizon."""
    gate = gate or load_edge_gate()
    h = PRIMARY_HORIZON_BARS
    row = summary[(summary["mode"] == mode) & (summary["timeframe"] == timeframe)
                  & (summary["horizon"] == h)]
    crit: dict = {}
    if row.empty or not row["n"].iloc[0]:
        crit["entries"] = (0, ">0", False)
        return GateVerdict(mode, timeframe, h, False, crit)
    r = row.iloc[0]
    rt_cost = round_trip_cost_bps(cost_class)

    hit_ok = r["hit_rate"] >= gate["min_hit_rate"]
    crit["hit_rate"] = (round(r["hit_rate"], 4), f">={gate['min_hit_rate']}", bool(hit_ok))

    t_ok = r["t_stat"] >= gate["min_fwd_return_t_stat"]
    crit["t_stat"] = (round(r["t_stat"], 2), f">={gate['min_fwd_return_t_stat']}", bool(t_ok))

    net = r["mean_bps"] - rt_cost
    cost_ok = net >= gate["fwd_return_must_exceed_cost_by_bps"]
    crit["net_of_cost_bps"] = (round(net, 2),
                               f">={gate['fwd_return_must_exceed_cost_by_bps']} (rt cost {rt_cost})",
                               bool(cost_ok))

    beat_rand = (r["mean_bps"] > r["rand_mean_bps"]) and (r["hit_rate"] > r["rand_hit_rate"])
    crit["beats_random"] = (
        f"mean {round(r['mean_bps'],2)} vs {round(r['rand_mean_bps'],2)}; "
        f"hit {round(r['hit_rate'],3)} vs {round(r['rand_hit_rate'],3)}",
        "real>random", bool(beat_rand))

    mono_ok = True
    if gate.get("require_monotonic_conviction", False):
        mono_ok = bool(mono.get(mode, {}).get("monotonic", False))
        crit["monotonic_conviction"] = (
            str(mono.get(mode, {}).get("buckets", [])), "non-decreasing", mono_ok)

    passed = all(ok for _, _, ok in crit.values())
    return GateVerdict(mode, timeframe, h, passed, crit)
