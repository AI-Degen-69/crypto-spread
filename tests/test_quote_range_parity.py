"""Issue #228: both engines judge the quotable range the same way, every tick.

Not the full parity harness (#214) -- this drives the two engines over one
shared snapshot sequence and compares the one decision this issue is about:
whether a quote is placed, held, or left standing when the two-sided mid is
inside, outside, or back inside `quote_range`.

The plumbing (`_snap`, `_drive_live`) is the #225 anchor-parity harness's,
imported rather than copied so the two cannot drift. The scenarios are this
issue's. Asserted on quotes and fills, never on status strings or markers:
live proves placement with order ids, the backtest with `entered` and fills
off a tape printed at the live engine's resting price.
"""
from __future__ import annotations

import pytest

from backtest.engine import BacktestParams, _simulate_window
from tests.test_entry_anchor_parity import (
    DN_TOKEN, UP_TOKEN, _drive_live, _snap,
)

OFFSET = 0.02


def _bt_params(**overrides):
    base = dict(offset=OFFSET, entry_timeout_pct=0.0,
                max_start_elapsed_pct=0.0)
    base.update(overrides)
    return BacktestParams(**base)


# Tick 0 opens at mid 0.92, outside the default (0.10, 0.90) range. Tick 1 is
# the same book, proving the hold is per-tick rather than a one-off decision.
OUTSIDE_OPEN = [
    _snap(0.0, 0.915, 0.925, 0.075, 0.085, recorded_mid=0.92),
    _snap(20.0, 0.915, 0.925, 0.075, 0.085, recorded_mid=0.92),
]

# The 0.92 open reverts to a 0.50 book on tick 1 and stays there.
LEAVE_AND_RETURN = OUTSIDE_OPEN[:1] + [
    _snap(20.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
    _snap(40.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
]

# A healthy 0.50 open, then the mid runs to 0.92 while the quote rests.
RESTING_THEN_OUTSIDE = [
    _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
    _snap(20.0, 0.915, 0.925, 0.075, 0.085, recorded_mid=0.92),
]


# Tick 0 opens at mid 0.08, outside the lower bound of (0.10, 0.90).
OUTSIDE_LOW_OPEN = [
    _snap(0.0, 0.075, 0.085, 0.915, 0.925, recorded_mid=0.08),
    _snap(20.0, 0.075, 0.085, 0.915, 0.925, recorded_mid=0.08),
]


def test_outside_range_at_open_places_nothing_in_either_engine():
    """Mid 0.92 at open: no quote on either leg, and nothing latched."""
    _engine, mstate = _drive_live(OUTSIDE_OPEN, offset=OFFSET)
    assert mstate.order_id_up is None and mstate.order_id_down is None
    assert mstate.order_status_up != "RESTING"
    assert mstate.order_status_down != "RESTING"
    assert mstate.entry_cancelled_timeout is False
    assert not mstate.filled_up and not mstate.filled_down

    w = _simulate_window(OUTSIDE_OPEN, _bt_params())
    assert w.entered is False
    assert not w.filled_up and not w.filled_down


def test_outside_range_low_at_open_places_nothing_in_either_engine():
    """Mid 0.08 at open (below quote_lo): no quote on either leg in both engines."""
    _engine, mstate = _drive_live(OUTSIDE_LOW_OPEN, offset=OFFSET)
    assert mstate.order_id_up is None and mstate.order_id_down is None
    assert mstate.order_status_up != "RESTING"
    assert mstate.order_status_down != "RESTING"
    assert not mstate.filled_up and not mstate.filled_down

    w = _simulate_window(OUTSIDE_LOW_OPEN, _bt_params())
    assert w.entered is False
    assert not w.filled_up and not w.filled_down


def test_return_inside_range_is_quoted_again_in_the_same_window():
    """The 0.92 open reverts to 0.50: both engines quote at the current mid."""
    _engine, mstate = _drive_live(LEAVE_AND_RETURN, offset=OFFSET)
    assert mstate.order_id_up is not None and mstate.order_id_down is not None
    assert mstate.resting_up == pytest.approx(0.48)
    assert mstate.resting_down == pytest.approx(0.48)

    snaps = [dict(s) for s in LEAVE_AND_RETURN]
    snaps[-1] = {**snaps[-1], "tape_delta": [
        {"asset": UP_TOKEN, "price": 0.48, "size": 10.0},
        {"asset": DN_TOKEN, "price": 0.48, "size": 10.0},
    ]}
    w = _simulate_window(snaps, _bt_params())
    assert w.pair_captured is True
    assert w.entry_price_up == pytest.approx(0.48)
    assert w.entry_price_down == pytest.approx(0.48)


def test_boundary_mid_010_is_inside():
    """The range is inclusive: a two-sided mid of exactly 0.10 is quotable."""
    snaps = [_snap(0.0, 0.095, 0.105, 0.895, 0.905, recorded_mid=0.10)]
    _engine, mstate = _drive_live(snaps, offset=OFFSET)
    assert mstate.order_id_up is not None and mstate.order_id_down is not None

    w = _simulate_window(snaps, _bt_params())
    assert w.entered is True


def test_boundary_mid_090_is_inside():
    """The range is inclusive: a two-sided mid of exactly 0.90 is quotable."""
    snaps = [_snap(0.0, 0.895, 0.905, 0.095, 0.105, recorded_mid=0.90)]
    _engine, mstate = _drive_live(snaps, offset=OFFSET)
    assert mstate.order_id_up is not None and mstate.order_id_down is not None

    w = _simulate_window(snaps, _bt_params())
    assert w.entered is True


def test_resting_quote_stands_while_the_mid_is_outside():
    """An already-resting quote is on the venue: the 0.92 mid cancels nothing.

    The stop is off so only the range can touch the orders. The 0.085 down ask
    crashes through the resting 0.48 down bid — the venue fills it, correctly —
    while the resting up leg stands uncancelled. The backtest fills both off
    the tape and captures the pair.
    """
    _engine, mstate = _drive_live(
        RESTING_THEN_OUTSIDE, offset=OFFSET, stop_loss_enabled=False)
    assert mstate.filled_down is True
    assert mstate.order_status_up == "RESTING"
    assert mstate.entry_cancelled_timeout is False

    snaps = [dict(s) for s in RESTING_THEN_OUTSIDE]
    snaps[-1] = {**snaps[-1], "tape_delta": [
        {"asset": UP_TOKEN, "price": 0.48, "size": 10.0},
        {"asset": DN_TOKEN, "price": 0.48, "size": 10.0},
    ]}
    w = _simulate_window(snaps, _bt_params())
    assert w.pair_captured is True


def test_narrow_custom_range_holds_placement_outside_it():
    """quote_range=(0.40, 0.60): mid 0.70 holds placement, mid 0.50 quotes."""
    out_then_in = [
        _snap(0.0, 0.695, 0.705, 0.295, 0.305, recorded_mid=0.70),
        _snap(20.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
    ]
    _engine, mstate = _drive_live(
        out_then_in[:1], OFFSET, quote_range=(0.40, 0.60))
    assert mstate.order_id_up is None and mstate.order_id_down is None

    _engine, mstate = _drive_live(
        out_then_in, OFFSET, quote_range=(0.40, 0.60))
    assert mstate.order_id_up is not None and mstate.order_id_down is not None
    assert mstate.resting_up == pytest.approx(0.48)

    w_held = _simulate_window(
        out_then_in[:1], _bt_params(quote_range=(0.40, 0.60)))
    assert w_held.entered is False

    snaps = [dict(s) for s in out_then_in]
    snaps[-1] = {**snaps[-1], "tape_delta": [
        {"asset": UP_TOKEN, "price": 0.48, "size": 10.0},
        {"asset": DN_TOKEN, "price": 0.48, "size": 10.0},
    ]}
    w = _simulate_window(snaps, _bt_params(quote_range=(0.40, 0.60)))
    assert w.pair_captured is True
