"""Hermetic tests for scripts/replay_shadow_check.py (issue #146).

Driver-only gates with no engine dependency: config-mirror drift rejection,
universe/time scope filtering, and pre-coverage window separation. Uses
synthetic snaps only — never the 700MB tick file or gitignored runs/ data.
"""
from __future__ import annotations

import dataclasses
import importlib

import pytest

mod = importlib.import_module("scripts.replay_shadow_check")

# Recorded shadow config, transcribed once from
# runs/paper/2026-09-11_22-10_IDT/data/final.json ["params"] (2026-09-13).
# The driver itself reads final.json live; this copy pins the test contract.
RECORDED = {
    "offset": 0.03,
    "exit_thresh": 0.05,
    "exit_thresh_naked": 0.05,
    "exit_reversal": 0.5,
    "shares": 5,
    "entry_timeout_pct": 1.0,
    "max_start_elapsed_pct": 0.1,
    "min_requote_remaining_sec": 300.0,
    "reentry_min_remaining_pct": 0.3,
    "max_reentries_per_window": 0,
    "reentry_drift_band": 0.015,
    "max_pair_cost": 0.98,
    "entry_delay_sec": 60.0,
    "entry_band": 0.04,
}


def test_mirror_accepts_recorded_config():
    """Exact recorded config passes the mirror gate on every leg."""
    for fill_model, gates_on in (("tape", True), ("book", True),
                                 ("tape", False), ("book", False)):
        params = mod.build_params(fill_model, gates_on)
        mod.assert_config_mirror(params, RECORDED, fill_model, gates_on)


def test_mirror_rejects_drift():
    """A single drifted knob fails the mirror gate."""
    params = mod.build_params("tape", True)
    drifted = dataclasses.replace(params, offset=0.99)
    with pytest.raises(AssertionError):
        mod.assert_config_mirror(drifted, RECORDED, "tape", True)


def test_mirror_rejects_wrong_leg():
    """Right params under the wrong leg label fail the mirror gate."""
    params = mod.build_params("book", True)
    with pytest.raises(AssertionError):
        mod.assert_config_mirror(params, RECORDED, "tape", True)


def test_exit_covers_universe():
    """Every universe series has an explicit 0.05 exit threshold."""
    params = mod.build_params("tape", True)
    for slug in mod.UNIVERSE:
        assert params.exit_thresh_by_slug.get(slug) == 0.05


def _snap(cid: str, start_ts: float, ts_off: float = 2.0,
          touch: float = 1.0) -> dict:
    return {"cid": cid, "ts": start_ts + ts_off, "start_ts": start_ts,
            "touch_pair": touch}


def test_select_groups_splits_pre_coverage():
    """Windows opening before coverage are excluded, in-range kept."""
    snaps = [_snap("old", mod.T0 - 3600.0), _snap("new", mod.T0 + 10.0)]
    included, excluded = mod.select_groups(snaps)
    assert [c for c, _ in included] == ["new"]
    assert excluded == {"pre_coverage": 1, "strict_late": 0, "touch_insane": 0}


def test_select_groups_boundary_inclusive():
    """A window opening exactly at coverage start counts as observed."""
    included, excluded = mod.select_groups([_snap("edge", mod.T0)])
    assert [c for c, _ in included] == ["edge"]
    assert excluded == {"pre_coverage": 0, "strict_late": 0, "touch_insane": 0}


def test_select_groups_strict_late():
    """First snap >2s after open excludes the window (Strict rule)."""
    snaps = [_snap("late", mod.T0 + 100.0, ts_off=30.0)]
    included, excluded = mod.select_groups(snaps + [_snap("ok", mod.T0 + 200.0)])
    assert [c for c, _ in included] == ["ok"]
    assert excluded["strict_late"] == 1


def test_select_groups_touch_insane():
    """A window with an insane touch pair is quarantined."""
    snaps = [_snap("wild", mod.T0 + 10.0, touch=1.64),
             _snap("wild", mod.T0 + 11.0, touch=1.01),
             _snap("ok", mod.T0 + 20.0)]
    included, excluded = mod.select_groups(snaps)
    assert [c for c, _ in included] == ["ok"]
    assert excluded["touch_insane"] == 1


def test_pair_cap_override_documented():
    """1.05 leg asserts against the override, not the recorded 0.98."""
    params = mod.build_params("book", True, 1.05)
    assert params.pair_cost_gate == 1.05
    mod.assert_config_mirror(params, RECORDED, "book", True, 1.05)
    with pytest.raises(AssertionError):
        mod.assert_config_mirror(params, RECORDED, "book", True, 0.98)


def test_select_groups_rejects_empty():
    """Empty scope fails fast instead of producing vacuous totals."""
    with pytest.raises(AssertionError):
        mod.select_groups([])
