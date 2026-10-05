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
    base = dict(offset=OFFSET, dead_zone_val=0.0)
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
    """Issue #456: the range is inclusive on the mid AND the legs.

    Mid exactly 0.10 with legs 0.08/0.88 under (0.05, 0.90): quotable —
    the boundary holds when the computed legs are inside too.
    """
    snaps = [_snap(0.0, 0.095, 0.105, 0.895, 0.905, recorded_mid=0.10)]
    _engine, mstate = _drive_live(snaps, offset=OFFSET, quote_range=(0.05, 0.90))
    assert mstate.order_id_up is not None and mstate.order_id_down is not None

    w = _simulate_window(snaps, _bt_params(quote_range=(0.05, 0.90)))
    assert w.entered is True


def test_boundary_mid_090_is_inside():
    """Issue #456: the range is inclusive on the mid AND the legs.

    Mid exactly 0.90 with legs 0.88/0.08 under (0.05, 0.90): quotable.
    """
    snaps = [_snap(0.0, 0.895, 0.905, 0.095, 0.105, recorded_mid=0.90)]
    _engine, mstate = _drive_live(snaps, offset=OFFSET, quote_range=(0.05, 0.90))
    assert mstate.order_id_up is not None and mstate.order_id_down is not None

    w = _simulate_window(snaps, _bt_params(quote_range=(0.05, 0.90)))
    assert w.entered is True


def test_resting_quote_stands_while_the_mid_is_outside():
    """The range cancels nothing, but the stop is always armed (#229).

    The 0.085 down ask crashes through the resting 0.48 down bid — the venue
    fills it, correctly — while the up leg stands uncancelled by the range.
    Issue #229 deleted `stop_loss_enabled`, so the armed stop (rules §2) is the
    only thing that can touch the position now: the 0.92 mid is 0.40 adverse
    to the DOWN entry, and both engines stop the naked leg out rather than
    holding it. The range itself is not what acted.
    """
    _engine, mstate = _drive_live(
        RESTING_THEN_OUTSIDE, offset=OFFSET, dead_zone_val=0.0)
    assert mstate.filled_down is True
    # The range did not cancel the standing up order — the stop did.
    assert mstate.exit_taken is True
    assert mstate.exit_side == "DOWN"
    assert mstate.entry_cancelled_timeout is False

    # Backtest parity: with the tape filling BOTH legs the pair completes on
    # the same tick, and a completed pair is never stopped.
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


# Issue #456: quote_range guards order prices, not just the mid. At offset
# 0.15 a mid of 0.19 rests the UP leg at 0.04 — outside (0.10, 0.90) — so the
# pair is rejected as a unit even though the mid itself is in range.
LEG_OFFSET = 0.15

# Mid 0.19, legs 0.04 / 0.66 at offset 0.15: UP leg out, DOWN leg in.
LEG_OUT_OF_RANGE = [
    _snap(0.0, 0.035, 0.045, 0.655, 0.665, recorded_mid=0.19),
    _snap(20.0, 0.035, 0.045, 0.655, 0.665, recorded_mid=0.19),
]

# The 0.19 open reverts to a 0.50 book: legs 0.35 / 0.35, quotable again.
LEG_OUT_THEN_IN = LEG_OUT_OF_RANGE[:1] + [
    _snap(20.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
    _snap(40.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
]


def test_leg_outside_range_places_nothing_in_either_engine():
    """Mid 0.19 in range, UP leg 0.04 out: neither leg is quoted, nothing latched."""
    _engine, mstate = _drive_live(LEG_OUT_OF_RANGE, offset=LEG_OFFSET)
    assert mstate.anchored_mid is None
    assert mstate.order_id_up is None and mstate.order_id_down is None
    assert mstate.order_status_up != "RESTING"
    assert mstate.order_status_down != "RESTING"
    assert not mstate.filled_up and not mstate.filled_down

    w = _simulate_window(LEG_OUT_OF_RANGE, _bt_params(offset=LEG_OFFSET))
    assert w.entered is False
    assert not w.filled_up and not w.filled_down


def test_leg_returns_inside_range_quotes_again_in_the_same_window():
    """The 0.19 open reverts to 0.50: both engines quote the pair at 0.35."""
    _engine, mstate = _drive_live(LEG_OUT_THEN_IN, offset=LEG_OFFSET)
    assert mstate.order_id_up is not None and mstate.order_id_down is not None
    assert mstate.resting_up == pytest.approx(0.35)
    assert mstate.resting_down == pytest.approx(0.35)

    snaps = [dict(s) for s in LEG_OUT_THEN_IN]
    snaps[-1] = {**snaps[-1], "tape_delta": [
        {"asset": UP_TOKEN, "price": 0.35, "size": 10.0},
        {"asset": DN_TOKEN, "price": 0.35, "size": 10.0},
    ]}
    w = _simulate_window(snaps, _bt_params(offset=LEG_OFFSET))
    assert w.pair_captured is True
    assert w.entry_price_up == pytest.approx(0.35)
    assert w.entry_price_down == pytest.approx(0.35)


def test_boundary_leg_010_is_inside():
    """The leg range is inclusive: a computed UP leg of exactly 0.10 is quotable."""
    snaps = [_snap(0.0, 0.095, 0.105, 0.595, 0.605, recorded_mid=0.25)]
    _engine, mstate = _drive_live(snaps, offset=LEG_OFFSET)
    assert mstate.order_id_up is not None and mstate.order_id_down is not None
    assert mstate.resting_up == pytest.approx(0.10)

    w = _simulate_window(snaps, _bt_params(offset=LEG_OFFSET))
    assert w.entered is True
