"""Unit tests for Issue #137: patient undecided-band maker preset.

Covers: engine knobs (entry_delay_sec, entry_band, stop_loss_enabled),
entry-delay gate, post-delay entry-band gate + telemetry, stop-loss-off
semantics, and the patient_band_maker preset incl. API wiring.
"""

from strategy.live_trader import LiveTraderEngine


# ============================================================================
# TASK 1: engine knobs — init + update_config + state echo
# ============================================================================

def test_issue137_knob_defaults_preserve_current_behavior():
    """New knobs default to off/True so default engine behavior is identical."""
    engine = LiveTraderEngine()
    assert engine.entry_delay_sec == 0.0
    assert engine.entry_band == 0.0
    assert engine.stop_loss_enabled is True
    assert engine.active_preset is None


def test_issue137_state_echoes_new_knobs():
    """get_state params echo the new knobs plus the active preset."""
    engine = LiveTraderEngine()
    params = engine.get_state()["params"]
    assert params["entry_delay_sec"] == 0.0
    assert params["entry_band"] == 0.0
    assert params["stop_loss_enabled"] is True
    assert engine.get_state()["active_preset"] is None


def test_issue137_update_config_sets_new_knobs():
    """update_config accepts and clamps the new knobs."""
    engine = LiveTraderEngine()
    engine.update_config(entry_delay_sec=60.0, entry_band=0.04, stop_loss_enabled=False)
    assert engine.entry_delay_sec == 60.0
    assert engine.entry_band == 0.04
    assert engine.stop_loss_enabled is False
    params = engine.get_state()["params"]
    assert params["entry_delay_sec"] == 60.0
    assert params["entry_band"] == 0.04
    assert params["stop_loss_enabled"] is False


def test_issue137_update_config_rejects_unknown_preset_atomically():
    """Unknown preset raises and leaves the whole configuration untouched."""
    engine = LiveTraderEngine()
    before = engine.get_state()["params"]
    try:
        engine.update_config(preset="no_such_preset")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown preset")
    after = engine.get_state()["params"]
    assert after == before
    assert engine.active_preset is None
