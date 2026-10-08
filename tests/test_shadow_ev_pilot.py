"""Issue #468: a pilot run's manifest records the engine's real knobs.

`config_hypothesis` must carry `exit_reversal` / `dead_zone_val` /
`dead_zone_unit` copied from the trading engine — not a hand-typed copy —
in both `data/meta.json` and `manifest.json.
"""

import asyncio
import json

from strategy.live_trader import LiveTraderEngine
from scripts import shadow_ev_pilot

TRIO = ("exit_reversal", "dead_zone_val", "dead_zone_unit")


def _run_zero_hour_pilot(tmp_path, monkeypatch):
    """Run the real amain flow with no hours and no network (start/stop off)."""
    monkeypatch.setattr(LiveTraderEngine, "start", lambda self: None)
    monkeypatch.setattr(
        LiveTraderEngine, "stop", lambda self, stop_streams=False: None)
    run_dir = tmp_path / "run"
    (run_dir / "data").mkdir(parents=True)
    (run_dir / "research-papers").mkdir(parents=True)
    asyncio.run(shadow_ev_pilot.amain(0, 60.0, 5, 1000.0, run_dir, 60.0))
    return run_dir


def _hypotheses(run_dir):
    meta = json.loads((run_dir / "data" / "meta.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    return meta["config_hypothesis"], manifest["config_hypothesis"]


def test_pilot_manifest_records_engine_knobs(tmp_path, monkeypatch):
    run_dir = _run_zero_hour_pilot(tmp_path, monkeypatch)
    expected = shadow_ev_pilot.build_engine(60.0, 5, 1000.0).get_state()["params"]
    for hyp in _hypotheses(run_dir):
        for key in TRIO:
            assert hyp[key] == expected[key]
    # The pinned pilot values, not the engine defaults (exit_reversal 0.02).
    assert _hypotheses(run_dir)[0]["exit_reversal"] == 0.50


def test_pilot_manifest_follows_changed_settings(tmp_path, monkeypatch):
    """A reconfigured engine is recorded as reconfigured — the record follows
    the engine rather than repeating a typed copy."""
    real_build = shadow_ev_pilot.build_engine

    def build_changed(delay, shares, starting_balance):
        eng = real_build(delay, shares, starting_balance)
        eng.update_config(exit_reversal=0.33, dead_zone_val=0.25,
                          dead_zone_unit="sec")
        return eng

    monkeypatch.setattr(shadow_ev_pilot, "build_engine", build_changed)
    run_dir = _run_zero_hour_pilot(tmp_path, monkeypatch)
    for hyp in _hypotheses(run_dir):
        assert hyp["exit_reversal"] == 0.33
        assert hyp["dead_zone_val"] == 0.25
        assert hyp["dead_zone_unit"] == "sec"
