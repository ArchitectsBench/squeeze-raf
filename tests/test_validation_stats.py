"""P6 sanity tests for the gauntlet statistics — they must behave correctly on
constructed data with known answers (a buggy DSR/PBO/permutation would wave through
an overfit strategy, which is the whole risk)."""

from __future__ import annotations

import numpy as np

from validation.bootstrap import block_bootstrap_sharpe
from validation.dsr import deflated_sharpe_ratio, expected_max_sharpe
from validation.pbo_cscv import pbo_cscv
from validation.permutation import permutation_test


def test_expected_max_sharpe_grows_with_trials():
    # more trials -> higher expected max (easier to find a high Sharpe by luck)
    e10 = expected_max_sharpe(0.01, 10)
    e1000 = expected_max_sharpe(0.01, 1000)
    assert e1000 > e10 > 0


def test_dsr_high_for_strong_signal_few_trials():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.005, 2000)  # strong positive per-bar Sharpe ~0.2
    out = deflated_sharpe_ratio(r, sr_variance_across_trials=1e-4, n_trials=5)
    assert out["dsr"] > 0.95 and out["significant"]


def test_dsr_low_for_noise_many_trials():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0, 0.01, 2000)  # zero edge
    out = deflated_sharpe_ratio(r, sr_variance_across_trials=0.05, n_trials=5000)
    assert not out["significant"]


def test_pbo_near_half_for_pure_noise():
    rng = np.random.default_rng(2)
    mat = rng.normal(0, 0.01, size=(1200, 20))  # 20 configs, all noise
    out = pbo_cscv(mat, s_blocks=10)
    # with no real edge, the IS-best is ~random OOS -> PBO around 0.5
    assert 0.30 <= out["pbo"] <= 0.70


def test_pbo_low_when_one_config_dominates():
    rng = np.random.default_rng(3)
    mat = rng.normal(0, 0.01, size=(1200, 20))
    mat[:, 0] += 0.004  # config 0 has a genuine, persistent edge
    out = pbo_cscv(mat, s_blocks=10)
    assert out["pbo"] < 0.2  # the real winner generalizes -> low overfit probability


def test_bootstrap_brackets_sharpe():
    rng = np.random.default_rng(4)
    r = rng.normal(0.0005, 0.01, 1500)
    out = block_bootstrap_sharpe(r, ann=252, n_iter=1000, seed=42)
    assert out["p5"] < out["median"] < out["p95"]


def test_permutation_detects_real_timing_skill():
    # position perfectly aligned with returns -> real Sharpe beats shifted null
    rng = np.random.default_rng(5)
    ret = rng.normal(0, 0.01, 1500)
    pos = np.sign(ret) * 1.0  # "skillful" (uses same-bar sign; for test of machinery)
    out = permutation_test([pos], [ret], ann=252, n_iter=300, seed=1)
    assert out["beats"] and out["percentile"] >= 95


def test_permutation_no_skill_random_positions():
    rng = np.random.default_rng(6)
    ret = rng.normal(0, 0.01, 1500)
    pos = rng.choice([-1.0, 0.0, 1.0], size=1500)  # random positions
    out = permutation_test([pos], [ret], ann=252, n_iter=300, seed=1)
    assert not out["beats"]
