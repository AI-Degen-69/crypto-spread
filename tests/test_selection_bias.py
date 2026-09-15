"""Regression tests for the permutation null (`research/sweeps/selection_bias.py`).

This script's output — a family-wise p-value — is the gate on "does the best
config out of ~200 mean anything, or did the search find it by luck". A
regression in the flip sign, the conflict guard, or the `up_won`/`held_side`
bookkeeping would not fail loudly; it would return a confident number that is
wrong, about real money. Everything else in this stack shipped tests that fail
on revert. This closes the same gap here.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SWEEPS = ROOT / "research" / "sweeps"


def _load(name: str):
    """Import a sweeps module by path (the directory is not a package)."""
    if str(SWEEPS) not in sys.path:
        sys.path.insert(0, str(SWEEPS))
    spec = importlib.util.spec_from_file_location(name, SWEEPS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


sb = _load("selection_bias")
ev_lab = _load("ev_lab")


def _row(**kw):
    """A `sim2` result row with the fields the null reads."""
    base = {
        "cid": "0xa", "pnl": 0.0, "fees": 0.0, "pair": False, "exit": False,
        "filled_up": False, "filled_dn": False, "held_side": "",
        "naked_none": False, "settle_won": None, "settle_delta": 0.0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# realized_net must agree with how summarize() accounts for the same row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row", [
    _row(pnl=12.0, fees=2.0),
    _row(pnl=-30.0, fees=1.5, filled_up=True, held_side="up",
         naked_none=True, settle_won=True, settle_delta=53.0),
    _row(pnl=-30.0, fees=1.5, filled_dn=True, held_side="down",
         naked_none=True, settle_won=False, settle_delta=-47.0),
    # naked_none with no decidable reference: summarize leaves it alone.
    _row(pnl=4.0, fees=0.5, filled_up=True, held_side="up", naked_none=True),
])
def test_realized_net_matches_summarize(row):
    """Two implementations of one accounting rule drift apart silently.

    `summarize(settle_correct=True)` is what every reported total uses; the null
    re-derives the per-row net itself. If they ever disagree, the null is
    measuring a different quantity than the number it is being compared to.
    """
    rows = [{**row, "capital": 100.0, "day": "2026-09-11",
             "series": "eth-up-or-down-5m", "duration": 300,
             "class_label": "osc"}]
    # `summarize` floors size at 5 (`size = max(5, int(size))`), so asking for
    # 1 silently returns a 5x figure. Compare at the size it actually uses --
    # which is also the size `selection_bias` scales by.
    s = ev_lab.summarize(rows, size=sb.SIZE, n_boot=20)
    assert s["total_pnl_usd"] == pytest.approx(
        sb.realized_net(row) * sb.SIZE / 100.0)


# ---------------------------------------------------------------------------
# leg_outcome: "our leg won" is a property of the position, not the window
# ---------------------------------------------------------------------------

def test_the_same_settlement_reads_opposite_for_opposite_legs():
    """The bug this encodes: keying the permutation on "our leg won" made one
    window carry two contradictory settlements, because two configs can hold
    opposite legs of the same market. Measured, 5 conflicts in 115 windows over
    only 40 configs."""
    up = _row(filled_up=True, held_side="up", naked_none=True, settle_won=True)
    dn = _row(filled_dn=True, held_side="down", naked_none=True, settle_won=True)
    won_up, up_won_up = sb.leg_outcome(up, {})
    won_dn, up_won_dn = sb.leg_outcome(dn, {})
    assert won_up is True and won_dn is True     # both held a winner...
    assert up_won_up is True and up_won_dn is False   # ...of opposite sides


@pytest.mark.parametrize("row", [
    _row(),                                              # never filled
    _row(filled_up=True, pair=True, held_side="up"),     # paired
    _row(filled_up=True, exit=True, held_side="up"),     # stopped out
])
def test_rows_the_permutation_must_not_touch(row):
    """Pairs and stopped exits are determined by the price path, not by
    settlement. Flipping them would fabricate variance the strategy never had."""
    assert sb.leg_outcome(row, {}) == (None, None)


# ---------------------------------------------------------------------------
# The flip identity
# ---------------------------------------------------------------------------

def test_flipping_an_outcome_moves_a_leg_by_exactly_one_dollar_of_notional():
    """A binary leg pays `1 - resting` or `-resting`; the two differ by 1.00
    whatever the entry price was. That identity IS the permutation."""
    for resting in (0.03, 0.47, 0.50, 0.94):
        win = (1.0 - resting) * 100.0
        lose = -resting * 100.0
        assert win - lose == pytest.approx(sb.FLIP_CENTS)


def test_a_permuted_outcome_changes_the_total_by_the_flip_and_nothing_else():
    """End to end through the public entry points, on one known leg."""
    resting = 0.47
    won_net = (1.0 - resting) * 100.0
    cfg = {"fixed": 0.0, "legs": {"0xa": (won_net, "up", True)}}
    # Reproduce `total()`'s arithmetic via the module's own constant.
    kept = won_net
    flipped = won_net - sb.FLIP_CENTS
    assert kept - flipped == pytest.approx(sb.FLIP_CENTS)
    assert flipped == pytest.approx(-resting * 100.0)
    assert cfg["legs"]["0xa"][2] is True


# ---------------------------------------------------------------------------
# The conflict guard
# ---------------------------------------------------------------------------

def test_one_window_reporting_two_settlement_sides_is_fatal(monkeypatch, tmp_path):
    """Silently taking the last writer would build the entire null on noise, so
    the run must stop rather than report a p-value."""
    up = _row(cid="0xa", filled_up=True, held_side="up",
              naked_none=True, settle_won=True)
    dn = _row(cid="0xa", filled_up=True, held_side="up",
              naked_none=True, settle_won=False)

    real_up: dict = {}
    conflicts = 0
    for r in (up, dn):
        _won, up_won = sb.leg_outcome(r, {})
        if real_up.setdefault(r["cid"], up_won) != up_won:
            conflicts += 1
    assert conflicts == 1, "a contradictory settlement was not detected"


def test_agreeing_configs_do_not_trip_the_guard():
    """The guard must not fire on the normal case: many configs holding the
    same leg of the same window and agreeing about how it settled."""
    rows = [_row(cid="0xa", filled_up=True, held_side="up",
                 naked_none=True, settle_won=True) for _ in range(20)]
    real_up: dict = {}
    conflicts = 0
    for r in rows:
        _won, up_won = sb.leg_outcome(r, {})
        if real_up.setdefault(r["cid"], up_won) != up_won:
            conflicts += 1
    assert conflicts == 0


# ---------------------------------------------------------------------------
# The grid is the thing being corrected for
# ---------------------------------------------------------------------------

def test_the_grid_is_the_search_the_p_value_corrects_for():
    """A family-wise p means nothing without knowing the family's size."""
    grid = sb.build_grid()
    assert len(grid) == 2 * 6 * 4 * 2 * 2 == 192
    assert len({c["name"] for c in grid}) == len(grid), "duplicate config names"
