"""Hermetic tests for scripts/replay_shadow_check.py (issue #146).

Driver-only gates with no engine dependency: config-mirror drift rejection,
universe/time scope filtering, and pre-coverage window separation. Uses
synthetic snaps only — never the 700MB tick file or gitignored runs/ data.
"""
from __future__ import annotations

import dataclasses
import datetime
import importlib
import json

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


# --- Issue #465 T2: scope a run that is not the recorded #146 one -----------

# The recorded #146 shadow ran hold-to-settle; #223 measured `close` and the
# engine default is `close` (strategy/live_trader.py:926). The frozen default
# stays "hold" so the reproduction is byte-identical, but the mirror must
# follow whatever a given run actually recorded.
def test_build_params_default_naked_leg_is_frozen_hold():
    """The frozen #146 reproduction replays under the policy it ran (hold)."""
    assert mod.build_params(True).naked_leg_at_expiry == "hold"
    assert mod.build_params(False, 1.00).naked_leg_at_expiry == "hold"


def test_naked_leg_mirror_follows_recorded_config():
    """A run that recorded `close` is mirrored under `close`, not the default."""
    recorded_close = dict(RECORDED, naked_leg_at_expiry="close")
    params = mod.build_params(True, naked_leg_at_expiry="close")
    mod.assert_config_mirror(params, recorded_close, True)
    with pytest.raises(AssertionError):
        # hold params under a close run must fail the mirror, not silently replay
        mod.assert_config_mirror(mod.build_params(True), recorded_close, True)


def test_default_scope_is_the_frozen_146_constants():
    """A bare invocation reproduces #146 byte-for-byte from module constants."""
    scope = mod.default_scope()
    assert scope.ticks_file == mod.TICKS_FILE
    assert scope.shadow_dir == mod.SHADOW_DIR
    assert scope.out_dir == mod.OUT_DIR
    assert scope.universe == mod.UNIVERSE
    assert (scope.t0, scope.t1) == (mod.T0, mod.T1)
    assert mod.resolve_scope("", "") == scope


def _write_run(run_dir, *, started: str, stopped: str,
               universe: tuple[str, ...] = ("xrp-up-or-down-15m",),
               start_in_final: bool = False) -> None:
    """Write a run dir in the shape the pilot actually records.

    The pilot stamps started_utc into data/meta.json (:142) and writes only
    stopped_utc into data/final.json — every recorded run on disk, including
    the #146 one, has no started_utc in final.json. `start_in_final` writes
    the legacy shape so the fallback stays covered.
    """
    data = run_dir / "data"
    data.mkdir(parents=True)
    (data / "meta.json").write_text(
        json.dumps({"started_utc": started,
                    "config_hypothesis": {"universe": list(universe)}}),
        encoding="utf-8")
    final = {"stopped_utc": stopped}
    if start_in_final:
        final["started_utc"] = started
    (data / "final.json").write_text(json.dumps(final), encoding="utf-8")


def test_derive_scope_reads_universe_and_times_from_run(tmp_path):
    """UNIVERSE+T0 come from meta.json, T1 from final.json, OUT_DIR stays inside."""
    run_dir = tmp_path / "2026-10-06_22-10_IDT"
    _write_run(run_dir, started="2026-10-06T22:10:54.616454+00:00",
               stopped="2026-10-07T09:10:58.928115+00:00",
               universe=("xrp-up-or-down-15m", "eth-up-or-down-5m"))
    ticks = tmp_path / "ticks_x.jsonl"
    scope = mod.derive_scope(run_dir, ticks)
    assert scope.universe == ("xrp-up-or-down-15m", "eth-up-or-down-5m")
    assert scope.out_dir == run_dir / "replay_comparison"
    assert scope.ticks_file == ticks
    assert scope.t0 < scope.t1
    assert scope.t1 == datetime.datetime.fromisoformat(
        "2026-10-07T09:10:58.928115+00:00").timestamp()


def test_derive_scope_reads_start_from_meta_when_final_lacks_it(tmp_path):
    """The recorded shape (no started_utc in final.json) still derives a scope.

    Regression for the T2 assumption that failed on the first real run: the
    driver must not depend on a key no writer has ever produced.
    """
    run_dir = tmp_path / "2026-10-06_17-48_UTC+03-00"
    _write_run(run_dir, started="2026-10-06T14:48:37.977556+00:00",
               stopped="2026-10-06T16:48:43.452185+00:00")
    final = json.loads((run_dir / "data" / "final.json").read_text(encoding="utf-8"))
    assert "started_utc" not in final, "fixture must match the recorded layout"
    scope = mod.derive_scope(run_dir, tmp_path / "ticks_x.jsonl")
    assert scope.t0 == datetime.datetime.fromisoformat(
        "2026-10-06T14:48:37.977556+00:00").timestamp()
    assert scope.t1 == datetime.datetime.fromisoformat(
        "2026-10-06T16:48:43.452185+00:00").timestamp()


def test_derive_scope_falls_back_to_final_start_stamp(tmp_path):
    """A legacy run dir carrying started_utc only in final.json still derives."""
    run_dir = tmp_path / "legacy"
    _write_run(run_dir, started="2026-10-06T22:10:00+00:00",
               stopped="2026-10-07T09:10:00+00:00", start_in_final=True)
    meta = json.loads((run_dir / "data" / "meta.json").read_text(encoding="utf-8"))
    del meta["started_utc"]
    (run_dir / "data" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    scope = mod.derive_scope(run_dir, tmp_path / "ticks_x.jsonl")
    assert scope.t0 == datetime.datetime.fromisoformat(
        "2026-10-06T22:10:00+00:00").timestamp()


def test_derive_scope_without_any_start_stamp_names_both_files(tmp_path):
    """No start stamp at all fails loudly, naming where it looked."""
    run_dir = tmp_path / "stampless"
    _write_run(run_dir, started="2026-10-06T22:10:00+00:00",
               stopped="2026-10-07T09:10:00+00:00")
    meta = json.loads((run_dir / "data" / "meta.json").read_text(encoding="utf-8"))
    del meta["started_utc"]
    (run_dir / "data" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(RuntimeError, match="started_utc"):
        mod.derive_scope(run_dir, tmp_path / "ticks_x.jsonl")


def test_derive_scope_without_a_universe_names_the_artifact(tmp_path):
    """A run dir whose meta.json carries no universe fails loudly, naming the key.

    The two stamp checks below raise a RuntimeError that names the file and the key;
    the universe read must fail the same way rather than surfacing a bare KeyError
    from nested indexing (the same unhelpful-failure class the T4 fix addressed).
    """
    run_dir = tmp_path / "nouniverse"
    _write_run(run_dir, started="2026-10-06T22:10:00+00:00",
               stopped="2026-10-07T09:10:00+00:00")
    meta = json.loads((run_dir / "data" / "meta.json").read_text(encoding="utf-8"))
    del meta["config_hypothesis"]
    (run_dir / "data" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(RuntimeError, match="config_hypothesis"):
        mod.derive_scope(run_dir, tmp_path / "ticks_x.jsonl")


def test_derive_scope_defaults_ticks_to_the_stop_day(tmp_path, monkeypatch):
    """Without --ticks, the coverage file named for the stop date is used."""
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    run_dir = tmp_path / "run"
    _write_run(run_dir, started="2026-10-06T22:10:00+00:00",
               stopped="2026-10-07T09:10:00+00:00")
    ticks_dir = tmp_path / "run" / "ticks"
    ticks_dir.mkdir(parents=True)
    (ticks_dir / "ticks_2026-10-07.jsonl").touch()
    scope = mod.derive_scope(run_dir)
    assert scope.ticks_file == ticks_dir / "ticks_2026-10-07.jsonl"


def test_derive_scope_missing_stop_day_ticks_names_the_flag(tmp_path, monkeypatch):
    """No coverage file for the stop day fails loudly and points at --ticks."""
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    run_dir = tmp_path / "run"
    _write_run(run_dir, started="2026-10-06T22:10:00+00:00",
               stopped="2026-10-07T09:10:00+00:00")
    with pytest.raises(RuntimeError, match="--ticks"):
        mod.derive_scope(run_dir)


def test_derive_scope_rejects_inverted_times(tmp_path):
    """A run whose stop predates its start is a corrupt artifact, not a scope."""
    run_dir = tmp_path / "bad"
    _write_run(run_dir, started="2026-10-07T09:10:00+00:00",
               stopped="2026-10-06T22:10:00+00:00")
    with pytest.raises(RuntimeError, match="postdate"):
        mod.derive_scope(run_dir, tmp_path / "ticks_x.jsonl")


def test_resolve_scope_ticks_only_keeps_frozen_universe_and_times(tmp_path):
    """--ticks alone re-scopes the coverage file without touching universe/times."""
    other = tmp_path / "ticks_other.jsonl"
    scope = mod.resolve_scope("", str(other))
    assert scope.ticks_file == other
    assert scope.universe == mod.UNIVERSE
    assert (scope.t0, scope.t1) == (mod.T0, mod.T1)
    assert scope.out_dir == mod.OUT_DIR


def _foreign_params(extra_slug="sol-up-or-down-5m", value=0.05):
    """build_params() plus one slug outside UNIVERSE (issue #467)."""
    base = mod.build_params(True)
    mapping = dict(base.exit_thresh_by_slug)
    mapping[extra_slug] = value
    return dataclasses.replace(base, exit_thresh_by_slug=mapping)


def test_mirror_checks_foreign_universe_slug():
    """A scope universe outside UNIVERSE is actually checked, not skipped."""
    params = _foreign_params()
    mod.assert_config_mirror(params, RECORDED, True,
                             universe=("sol-up-or-down-5m",))


def test_mirror_fails_foreign_slug_without_threshold():
    """A scope slug with no exit threshold fails the mirror gate."""
    params = mod.build_params(True)
    with pytest.raises(AssertionError, match="exit mirror gap: sol-up-or-down-5m"):
        mod.assert_config_mirror(params, RECORDED, True,
                                 universe=("sol-up-or-down-5m",))


def test_mirror_fails_foreign_slug_wrong_value():
    """A scope slug with a drifted threshold fails the mirror gate."""
    params = _foreign_params(value=0.09)
    with pytest.raises(AssertionError, match="exit mirror gap: sol-up-or-down-5m"):
        mod.assert_config_mirror(params, RECORDED, True,
                                 universe=("sol-up-or-down-5m",))


def test_mirror_default_still_checks_frozen_universe():
    """No universe given: the frozen UNIVERSE is checked, extras ignored."""
    params = _foreign_params(extra_slug="sol-up-or-down-5m", value=0.99)
    mod.assert_config_mirror(params, RECORDED, True)
