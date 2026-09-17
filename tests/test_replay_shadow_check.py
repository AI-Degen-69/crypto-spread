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
    # Issue #229: entry_timeout_pct / max_start_elapsed_pct were deleted from
    # the engine, so the mirror no longer compares them; the shadow's dead
    # zone (default 0.10 pct) is mirrored implicitly by build_params().
    "max_pair_cost": 0.98,
    "entry_delay_sec": 60.0,
}


def test_mirror_accepts_recorded_config():
    """Exact recorded config passes the mirror gate on every leg."""
    for gates_on in (True, False):
        params = mod.build_params(gates_on)
        mod.assert_config_mirror(params, RECORDED, gates_on)


def test_mirror_rejects_drift():
    """A single drifted knob fails the mirror gate."""
    params = mod.build_params(True)
    drifted = dataclasses.replace(params, offset=0.99)
    with pytest.raises(AssertionError):
        mod.assert_config_mirror(drifted, RECORDED, True)


def test_mirror_rejects_wrong_leg():
    """Right params under the wrong leg label fail the mirror gate.

    The fill model used to be the label that could be wrong; with one fill
    rule (#226) the remaining label is the gates.
    """
    params = mod.build_params(gates_on=False)
    with pytest.raises(AssertionError):
        mod.assert_config_mirror(params, RECORDED, gates_on=True)


def test_exit_covers_universe():
    """Every universe series has an explicit 0.05 exit threshold."""
    params = mod.build_params(True)
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


def test_select_groups_grandfathered_exact_t0_only():
    """Grandfathering applies strictly to start_ts == T0, not boundary-adjacent starts."""
    t0_exact = _snap("t0_exact", mod.T0, ts_off=2.1)
    after_t0 = _snap("after_t0", mod.T0 + 0.5, ts_off=2.5)
    before_t0 = _snap("before_t0", mod.T0 - 0.5, ts_off=2.5)

    included, excluded = mod.select_groups([t0_exact, after_t0, before_t0])
    assert [c for c, _ in included] == ["t0_exact"]
    assert excluded["strict_late"] == 2
    assert excluded["pre_coverage"] == 0


def test_select_groups_touch_insane():
    """A window with an insane touch pair is quarantined."""
    snaps = [_snap("wild", mod.T0 + 10.0, touch=1.64),
             _snap("wild", mod.T0 + 11.0, touch=1.01),
             _snap("ok", mod.T0 + 20.0)]
    included, excluded = mod.select_groups(snaps)
    assert [c for c, _ in included] == ["ok"]
    assert excluded["touch_insane"] == 1


def test_pair_cap_override_documented():
    """The loose leg asserts against the override, not the recorded 0.98.

    It was 1.05 until issue #227 hard-capped the knob at 1.00; 1.00 is now the
    loosest cap the engine accepts and plays the same role in the 2x2.
    """
    params = mod.build_params(True, 1.00)
    assert params.max_pair_cost == 1.00
    mod.assert_config_mirror(params, RECORDED, True, 1.00)
    with pytest.raises(AssertionError):
        mod.assert_config_mirror(params, RECORDED, True, 0.98)


def test_every_main_loop_cap_is_engine_legal():
    """main()'s legs must construct: a stale cap above 1.00 crashes the run.

    The loop draws from PAIR_CAPS, so pin both ends — every listed cap builds,
    and the old 1.05 raises instead of silently simulating an illegal cap.
    """
    for cap in mod.PAIR_CAPS:
        assert mod.build_params(True, cap).max_pair_cost == cap
        assert mod.build_params(False, cap).max_pair_cost == cap
    with pytest.raises(ValueError):
        mod.build_params(True, 1.05)


def test_select_groups_rejects_empty():
    """Empty scope fails fast instead of producing vacuous totals."""
    with pytest.raises(AssertionError):
        mod.select_groups([])
