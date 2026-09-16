"""Issue #226: both engines answer "did our quote fill" with the same rule.

Not the full parity harness (#214) -- this drives the two engines over one
shared snapshot sequence and compares the one decision this issue is about:
whether a resting buy filled, and at what price.

The plumbing (`_snap`, `_drive_live`) is the #225 anchor-parity harness's,
imported rather than copied so the two cannot drift. The scenarios are this
issue's.
"""
from __future__ import annotations

import pytest

from backtest.engine import BacktestParams, _simulate_window
from tests.test_entry_anchor_parity import (
    DN_TOKEN, SLUG, UP_TOKEN, _drive_live, _snap,
)

OFFSET = 0.02
# A 0.50 book quotes both legs at 0.48. Everything below moves one ask around
# that price: 0.48 is a touch, 0.479 is fully through it.
PRICED = dict(up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505)


def _params() -> BacktestParams:
    """Gates off, so only the fill rule can decide the outcome."""
    return BacktestParams(offset=OFFSET, entry_timeout_pct=0.0,
                          max_start_elapsed_pct=0.0,
                          enable_leg_chase=False, stop_loss_enabled=False,
                          naked_leg_timeout_pct=0.0)


def _tape(price: float) -> list[dict]:
    return [{"asset": UP_TOKEN, "price": price, "size": 5.0}]


def _place_then(up_ask: float, tape: list[dict] | None = None) -> list[dict]:
    """Tick 0 rests the quote under an untouchable ask; tick 1 is the event."""
    return [
        _snap(0.0, 0.495, 0.505, 0.495, 0.505, recorded_mid=0.50),
        _snap(1.0, 0.495, up_ask, 0.495, 0.505, recorded_mid=0.50, tape=tape),
    ]


def _drive_live_with_print(snaps: list[dict], price: float):
    """Live takes its prints from the socket, not from the poll payload."""
    engine, mstate = _drive_live(snaps, offset=OFFSET)
    engine._try_ws_tape_fill(mstate, "UP", {"price": price, "size": 5.0}, price)
    return engine, mstate


# --- 1. a print at our price ------------------------------------------------

def test_a_print_at_our_price_fills_both_engines_at_our_price():
    snaps = _place_then(up_ask=0.505, tape=_tape(0.48))

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True
    assert w.entry_price_up == pytest.approx(0.48)

    _engine, mstate = _drive_live_with_print(snaps, 0.48)
    assert mstate.filled_up is True
    assert mstate.fill_price_up == pytest.approx(0.48)


# --- 2. an ask fully through our price --------------------------------------

def test_an_ask_fully_through_fills_both_engines_at_our_price():
    """Not at the ask: the seller crossed into us, so we were the maker."""
    snaps = _place_then(up_ask=0.479)

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True
    assert w.entry_price_up == pytest.approx(0.48)

    _engine, mstate = _drive_live(snaps, offset=OFFSET)
    assert mstate.filled_up is True
    assert mstate.fill_price_up == pytest.approx(0.48)


# --- 3. a touch against a quote that is already resting ---------------------

def test_a_touch_fills_neither_engine():
    """Other bids may sit ahead of ours at an equal price. The backtest used
    to fill here under `fill_model="book"`, and live filled here always."""
    snaps = _place_then(up_ask=0.48)

    w = _simulate_window(snaps, _params())
    assert w.filled_up is False

    _engine, mstate = _drive_live(snaps, offset=OFFSET)
    assert mstate.filled_up is False


# --- 4. a quote placed onto a standing ask ----------------------------------

def test_a_quote_placed_onto_a_standing_ask_fills_in_both_engines():
    """The touch rule protects a quote that had to join the queue. One placed
    at a price the market is already offering matches on arrival instead, and
    both engines say so on the tick it is placed."""
    # A single tick. Both legs are quoted 0.47/0.48, so the two-sided mid is
    # still 0.50 and both quotes rest at exactly the 0.48 already on offer --
    # a touch, on the tick they are placed.
    snaps = [_snap(0.0, 0.47, 0.48, 0.47, 0.48, recorded_mid=0.50)]

    w = _simulate_window(snaps, _params())
    assert w.filled_up is True and w.filled_down is True
    assert w.pair_captured is True
    assert w.entry_price_up == pytest.approx(0.48)
    assert w.entry_price_down == pytest.approx(0.48)

    # Live pairs on the same tick and immediately re-quotes the next round,
    # which clears its per-round `filled_*` flags -- so the fill is asserted
    # where it lands permanently, on the merge count.
    _engine, mstate = _drive_live(snaps, offset=OFFSET)
    assert mstate.pairs_count == 1
    assert round(mstate.realized_pnl_usd, 2) == 0.20


# --- 5. the entry itself never pays a fee -----------------------------------

def test_an_entry_fill_adds_no_fee_in_either_engine():
    """Both triggers book the same entry and neither charges for it. The
    backtest's `fees_cents` here is the settlement charge alone, identical to
    a run of the same window where nothing ever filled."""
    by_print = _simulate_window(_place_then(0.505, _tape(0.48)), _params())
    by_book = _simulate_window(_place_then(0.479), _params())
    assert by_print.filled_up is True and by_book.filled_up is True
    assert by_print.entry_price_up == by_book.entry_price_up
    assert by_print.fees_cents == pytest.approx(by_book.fees_cents)

    # Live books no fee either. Asserted against the money — a fee deducted
    # inside the fill branches would move `realized_pnl_usd` without ever
    # touching the engine's (unused) `taker_fee_rate`.
    engine, mstate = _drive_live(_place_then(0.479), offset=OFFSET)
    assert mstate.filled_up is True
    assert mstate.realized_pnl_usd == 0.0
    assert engine.taker_fee_rate == 0.0
