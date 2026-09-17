"""Issue #227: both engines stop the leg chase at the same price.

Not the full parity harness (#214) -- this drives the two engines over one
shared snapshot sequence and compares the one decision this issue is about:
how high the chase may lift the unfilled leg's quote.

The plumbing (`_snap`, `_drive_live`) is the #225 anchor-parity harness's,
imported rather than copied so the two cannot drift. The scenarios are this
issue's.

Both engines call `book_math.chase_cap`, so these are not really independent
implementations any more -- that is the point. The test pins the *behaviour*
the shared formula produces at the two engines' call sites, which is where the
six inline copies used to disagree.
"""
from __future__ import annotations

import pytest

from backtest.engine import BacktestParams, _simulate_window
from strategy.book_math import chase_cap
from tests.test_entry_anchor_parity import _drive_live, _snap

OFFSET = 0.02
CAP = 0.99

# A 0.50 book rests both legs at 0.48. Tick 0 places them under untouchable
# asks; tick 1 drops the UP ask fully through 0.48, filling UP at 0.48 and
# leaving DOWN naked for the chase to lift. `entry_up` is therefore 0.48 and
# the ceiling is floor((0.99 - 0.48) * 100) / 100 = 0.51.
ENTRY = 0.48
CEILING = 0.51


def _params(**over) -> BacktestParams:
    """Every gate off, chase on, so only the ceiling can decide the outcome."""
    # Issue #229: the deleted timeout/stop knobs are gone; the dead zone
    # (0.0) is disabled so only the ceiling decides the outcome.
    base = dict(offset=OFFSET, dead_zone_val=0.0,
                enable_leg_chase=True, max_pair_cost=CAP)
    base.update(over)
    return BacktestParams(**base)


def _chase_window(dn_ask_after: float) -> list[dict]:
    """Rest at 0.48/0.48, fill UP on tick 1, then offer DOWN at `dn_ask_after`.

    Tick 2 repeats tick 1's DOWN ask so the chase has a tick on which to run
    with UP already filled; the backtest chases before fill detection, live
    chases both before and inside its fill block.
    """
    return [
        _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
        _snap(20.0, 0.470, 0.479, 0.495, dn_ask_after, recorded_mid=0.50),
        _snap(40.0, 0.470, 0.479, 0.495, dn_ask_after, recorded_mid=0.50),
    ]


def _live_chase(snaps: list[dict]):
    _engine, mstate = _drive_live(snaps, offset=OFFSET,
                                  enable_leg_chase=True, max_pair_cost=CAP)
    return mstate


def test_the_ceiling_is_the_same_number_in_both_engines():
    """Sanity: the shared formula is what both call sites are built around."""
    assert chase_cap(CAP, ENTRY) == CEILING


def test_an_ask_below_the_ceiling_is_chased_all_the_way_to_the_ask():
    """Nothing stops the chase before the ask when the ask is affordable.

    Asserted on the price, not on `chased_leg`: the chase lands on the ask,
    which is marketable, so the pair completes and live clears the marker when
    it requotes after the merge. The DOWN leg resting at 0.50 when it was
    placed at 0.48 is the chase, whatever the marker says afterwards.
    """
    snaps = _chase_window(dn_ask_after=0.50)

    mstate = _live_chase(snaps)
    assert mstate.filled_up is True, "the window never entered — fixture is vacuous"
    assert mstate.filled_down is True, "the chase never completed the pair"
    assert mstate.fill_price_down == pytest.approx(0.50)

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True, "the window never entered — fixture is vacuous"
    assert w.filled_down is True, "the chase never completed the pair"
    assert w.chased_leg == "down", "the chase never ran"
    assert w.chased_resting == pytest.approx(0.50)


def test_an_ask_above_the_ceiling_stops_both_engines_at_the_ceiling():
    """The ask is 0.60; both stop at 0.51 and neither goes a cent past it."""
    snaps = _chase_window(dn_ask_after=0.60)

    mstate = _live_chase(snaps)
    assert mstate.filled_up is True
    assert mstate.chased_leg == "DOWN", "the chase never ran"
    assert mstate.resting_down == pytest.approx(CEILING)

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True
    assert w.chased_leg == "down", "the chase never ran"
    assert w.chased_resting == pytest.approx(CEILING)


def test_neither_engine_lets_the_completed_pair_breach_the_cap():
    """The property the ceiling exists for, asserted on both engines at once.

    A binary pair settles at 1.00. `entry + chased` over the cap is a pair that
    cannot pay for itself, and there is no ask wide enough to make it happen.
    """
    for dn_ask_after in (0.50, 0.55, 0.60, 0.95):
        snaps = _chase_window(dn_ask_after)

        mstate = _live_chase(snaps)
        live_pair = mstate.fill_price_up + mstate.resting_down
        assert live_pair <= CAP + 1e-9, (
            f"live paired at {live_pair} over an ask of {dn_ask_after}")

        w = _simulate_window(snaps, _params())
        bt_pair = w.entry_price_up + w.chased_resting
        assert bt_pair <= CAP + 1e-9, (
            f"backtest paired at {bt_pair} over an ask of {dn_ask_after}")

        assert mstate.resting_down == pytest.approx(w.chased_resting), (
            f"the two engines chased to different prices at ask {dn_ask_after}")


def test_an_entry_above_the_cap_leaves_nothing_to_chase_with():
    """A leg bought at more than the whole pair may cost stops the chase dead.

    `chase_cap(0.50, 0.48)` is 0.02, below the 0.48 the DOWN leg already rests
    at, so `min(ask, cap)` never exceeds the resting quote and neither engine
    raises it. The cap is the smallest the range allows, which is the only way
    to reach this state without an out-of-range value.
    """
    snaps = _chase_window(dn_ask_after=0.60)

    _engine, mstate = _drive_live(snaps, offset=OFFSET,
                                  enable_leg_chase=True, max_pair_cost=0.50)
    assert mstate.filled_up is True
    assert mstate.chased_leg is None, "the chase ran past an exhausted cap"
    assert mstate.resting_down == pytest.approx(ENTRY)

    w = _simulate_window(snaps, _params(max_pair_cost=0.50))
    assert w.filled_up is True
    assert not w.chased_leg, "the chase ran past an exhausted cap"


def test_the_cap_is_refused_above_one_in_both_engines():
    """A pair settles at 1.00; a cap above it authorises a guaranteed loss."""
    with pytest.raises(ValueError):
        BacktestParams(max_pair_cost=1.05)

    from strategy.live_trader import LiveTraderEngine

    engine = LiveTraderEngine(load_persisted=False)
    engine.update_config(max_pair_cost=1.05)
    assert engine.max_pair_cost == 1.00


def test_the_cap_has_no_off_switch_or_nan_hole():
    """None, NaN and infinities are refused, not silently skipped.

    The field is a plain float: a None that slipped past validation would
    crash the chase at `min(ask, None)`, and a NaN would poison every
    comparison it touches. There is no "off" — 0.0 is out of range.
    """
    for bad in (None, float("nan"), float("inf"), float("-inf"), 0.0, True):
        with pytest.raises(ValueError):
            BacktestParams(max_pair_cost=bad)
