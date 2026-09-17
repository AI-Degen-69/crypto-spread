"""Issue #231: Rule: the leg chase escalates with time instead of firing on the first tick.

Tests parity between backtest/engine.py and strategy/live_trader.py for the
escalation ladder specified in docs/engine-decision-rules.md §12:

    max_affordable = floor((max_pair_cost - entry_price_of_filled_leg) * 100) / 100
    progress       = clamp((now - went_naked_at) / (dead_zone_start - went_naked_at), 0, 1)
    ceiling        = original_resting + progress * (max_affordable - original_resting)
    target         = min(other_leg_ask, ceiling)
"""
from __future__ import annotations

import pytest

from backtest.engine import BacktestParams, _simulate_window
from tests.test_entry_anchor_parity import _drive_live, _snap

OFFSET = 0.02
CAP = 0.99
ENTRY = 0.48
CEILING = 0.51


def _params(**over) -> BacktestParams:
    base = dict(offset=OFFSET, dead_zone_val=0.10,
                enable_leg_chase=True, max_pair_cost=CAP)
    base.update(over)
    return BacktestParams(**base)


def _live_chase(snaps: list[dict], **over):
    params = dict(offset=OFFSET, dead_zone_val=0.10,
                  enable_leg_chase=True, max_pair_cost=CAP)
    params.update(over)
    _engine, mstate = _drive_live(snaps, **params)
    return mstate


def test_parity_first_tick_after_fill_maintains_patience():
    """On the tick immediately after fill (progress == 0), neither engine lifts the quote."""
    # Window duration = 300s, dead zone cutoff = 30s (starts at 270s).
    # t=0: opening quote resting at 0.48/0.48
    # t=20: UP fills at 0.48. DOWN ask is 0.50 (above resting 0.48).
    # t=21: 1s later. Market is unmoved.
    snaps = [
        _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
        _snap(20.0, 0.470, 0.479, 0.495, 0.50, recorded_mid=0.50),
        _snap(21.0, 0.470, 0.479, 0.495, 0.50, recorded_mid=0.50),
    ]

    mstate = _live_chase(snaps)
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.resting_down == pytest.approx(0.48)
    assert mstate.chased_leg is None

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True
    assert w.filled_down is False
    assert w.chased_leg == ""
    assert mstate.resting_down == pytest.approx(w.chased_resting if w.chased_resting is not None else 0.48)


def test_parity_time_progress_scales_ceiling_smoothly():
    """At t=145s (50% progress between t=20s and t=270s), ceiling reaches 0.49 in both."""
    # went_naked_at = 20s, dead_zone_start = 270s -> span = 250s.
    # At t=145s: elapsed from naked = 125s -> progress = 125/250 = 0.50.
    # max_affordable = 0.51, orig = 0.48 -> ceiling = 0.48 + 0.50 * 0.03 = 0.495 -> floor to 0.49.
    # DOWN ask is 0.55 (wide), so quote stops at ceiling 0.49.
    snaps = [
        _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
        _snap(20.0, 0.470, 0.479, 0.495, 0.55, recorded_mid=0.50),
        _snap(145.0, 0.470, 0.479, 0.495, 0.55, recorded_mid=0.50),
    ]

    mstate = _live_chase(snaps)
    assert mstate.filled_up is True
    assert mstate.chased_leg == "DOWN"
    assert mstate.resting_down == pytest.approx(0.49)

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True
    assert w.chased_leg == "down"
    assert w.chased_resting == pytest.approx(0.49)
    assert mstate.resting_down == pytest.approx(w.chased_resting)


def test_parity_full_ceiling_at_dead_zone():
    """At t=270s (dead zone cutoff), progress == 1.0 and both engines reach CEILING (0.51)."""
    snaps = [
        _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
        _snap(20.0, 0.470, 0.479, 0.495, 0.60, recorded_mid=0.50),
        _snap(270.0, 0.470, 0.479, 0.495, 0.60, recorded_mid=0.50),
    ]

    mstate = _live_chase(snaps)
    assert mstate.filled_up is True
    assert mstate.chased_leg == "DOWN"
    assert mstate.resting_down == pytest.approx(CEILING)

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True
    assert w.chased_leg == "down"
    assert w.chased_resting == pytest.approx(CEILING)
    assert mstate.resting_down == pytest.approx(w.chased_resting)


def test_parity_pair_completion_at_dead_zone():
    """At dead zone, an ask meeting the ceiling completes the pair in both engines."""
    snaps = [
        _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
        _snap(20.0, 0.470, 0.479, 0.495, 0.51, recorded_mid=0.50),
        _snap(270.0, 0.470, 0.479, 0.495, 0.51, recorded_mid=0.50),
    ]

    mstate = _live_chase(snaps)
    assert mstate.filled_up is True
    assert mstate.filled_down is True
    assert mstate.fill_price_down == pytest.approx(0.51)

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True
    assert w.filled_down is True
    assert w.chased_resting == pytest.approx(0.51)
    assert (w.entry_price_up + w.chased_resting) <= CAP + 1e-9


def test_parity_quote_never_lowered_when_ask_drops():
    """Once raised to 0.49 at t=145s, a subsequent ask drop to 0.45 does not lower the bid."""
    snaps = [
        _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
        _snap(20.0, 0.470, 0.479, 0.495, 0.55, recorded_mid=0.50),
        _snap(145.0, 0.470, 0.479, 0.495, 0.55, recorded_mid=0.50),
        _snap(150.0, 0.470, 0.479, 0.495, 0.45, recorded_mid=0.50),
    ]

    mstate = _live_chase(snaps)
    assert mstate.resting_down >= 0.49 - 1e-9

    w = _simulate_window(snaps, _params())
    assert w.chased_resting >= 0.49 - 1e-9
    assert mstate.resting_down == pytest.approx(w.chased_resting)
