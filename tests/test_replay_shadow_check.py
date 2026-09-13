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


def _snap(cid: str, start_ts: float) -> dict:
    return {"cid": cid, "ts": start_ts + 2.0, "start_ts": start_ts}


def test_select_groups_splits_pre_coverage():
    """Windows opening before coverage are excluded, in-range kept."""
    snaps = [_snap("old", mod.T0 - 3600.0), _snap("new", mod.T0 + 10.0)]
    included, excluded = mod.select_groups(snaps)
    assert [c for c, _ in included] == ["new"]
    assert excluded == 1


def test_select_groups_boundary_inclusive():
    """A window opening exactly at coverage start counts as observed."""
    included, excluded = mod.select_groups([_snap("edge", mod.T0)])
    assert [c for c, _ in included] == ["edge"]
    assert excluded == 0


def test_select_groups_rejects_empty():
    """Empty scope fails fast instead of producing vacuous totals."""
    with pytest.raises(AssertionError):
        mod.select_groups([])
