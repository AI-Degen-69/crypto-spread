"""Shared book arithmetic (issue #170).

Before `strategy/book_math.py` the collector, the backtest engine, the live
trader and the sweep lab each carried their own copy of `mid` and
`queue_ahead`, and the copies disagreed on exactly the degenerate books that
decide entries. These tests pin the agreed semantics so a future copy cannot
quietly diverge again.
"""
from __future__ import annotations

import pytest

from strategy.book_math import mid, pair_cost, queue_ahead, two_sided_mid


def _book(bb=None, ba=None, bids=None, asks=None) -> dict:
    """Book dict in the shape every consumer already passes around."""
    return {"best_bid": bb, "best_ask": ba,
            "bids": bids or {}, "asks": asks or {}}


# --- mid -----------------------------------------------------------------

def test_mid_is_the_midpoint_of_a_two_sided_book():
    assert mid(_book(0.48, 0.52)) == 0.50
    assert mid(_book(0.10, 0.20)) == pytest.approx(0.15)


def test_mid_of_an_empty_book_is_unknown():
    assert mid(_book()) is None
    assert mid({}) is None


def test_mid_never_leaves_the_probability_range():
    """These are binary outcome tokens: a 'probability' of 1.003 is not one.

    This is the drift that motivated the module. The collector clamped, the
    backtest engine did not, and the two ran over the same tick files.
    """
    assert mid(_book(bb=0.998)) == 1.0        # engine used to give 1.003
    assert mid(_book(ba=0.002)) == 0.0        # engine used to give -0.003
    assert mid(_book(bb=1.0)) == 1.0
    assert mid(_book(ba=0.0)) == 0.0


def test_mid_nudges_a_one_sided_book_toward_the_missing_side():
    """A lone bid implies the true mid sits above it, and vice versa."""
    assert mid(_book(bb=0.40)) == pytest.approx(0.405)
    assert mid(_book(ba=0.60)) == pytest.approx(0.595)


def test_mid_clamps_a_malformed_two_sided_book():
    """A venue quote outside [0, 1] must not propagate into a resting price."""
    assert mid(_book(0.99, 1.40)) == 1.0
    assert mid(_book(-0.40, 0.01)) == pytest.approx(0.0, abs=1e-9)


# --- queue_ahead ---------------------------------------------------------

def test_queue_ahead_sums_levels_at_or_above_the_resting_price():
    bids = {0.50: 10.0, 0.48: 25.0, 0.47: 100.0}
    assert queue_ahead(bids, 0.48) == 35.0
    assert queue_ahead(bids, 0.50) == 10.0
    assert queue_ahead(bids, 0.01) == 135.0


def test_queue_ahead_of_an_empty_book_is_unknown_not_zero():
    """Issue #138: a degenerate book must not masquerade as front-of-queue.

    The live engine has returned None here since #138. The collector, the
    backtest engine, the sweep lab and two scripts all returned 0.0, and
    `0.0 <= queue_gate` passes — so the backtest took entries on books with
    no bids at all, which live would have refused.
    """
    assert queue_ahead({}, 0.48) is None
    assert queue_ahead(None, 0.48) is None


def test_queue_ahead_survives_string_keyed_books():
    """Tick files round-trip through JSON, so price keys come back as strings."""
    assert queue_ahead({"0.50": 10.0, "0.48": 25.0}, 0.48) == 35.0


def test_queue_ahead_of_an_unparseable_book_is_unknown():
    assert queue_ahead({"not-a-price": 10.0}, 0.48) is None


def test_queue_ahead_with_no_level_at_or_above_is_zero_not_unknown():
    """A real book that simply has no depth above us is a known zero."""
    assert queue_ahead({0.40: 10.0}, 0.48) == 0.0


# --- two_sided_mid -------------------------------------------------------

def test_two_sided_mid_combines_both_legs():
    got = two_sided_mid(_book(0.48, 0.52), _book(0.48, 0.52))
    assert got == 0.50


def test_two_sided_mid_is_unknown_when_either_leg_is_one_sided():
    """`backtest/engine.py` claimed to mirror the live engine here and did not.

    Live fell back to `bid or ask or 0.50`, inventing a number from a leg it
    could not actually price; the engine returned None. None is the honest
    answer, and it is what the adverse-open gate (#92) is safe against -- a
    fabricated 0.50 reads as "perfectly balanced" and opens the gate.
    """
    assert two_sided_mid(_book(0.48, None), _book(0.48, 0.52)) is None
    assert two_sided_mid(_book(0.48, 0.52), _book(None, 0.52)) is None
    assert two_sided_mid(_book(), _book()) is None


def test_two_sided_mid_does_not_read_a_zero_price_as_missing():
    """`bid or ask or 0.50` treated a legitimate 0.0 quote as absent."""
    got = two_sided_mid(_book(0.0, 0.02), _book(0.96, 1.0))
    assert got is not None
    assert got == pytest.approx((0.01 + (1.0 - 0.98)) / 2.0, abs=1e-4)


# --- pair_cost -----------------------------------------------------------

def test_pair_cost_adds_both_resting_legs():
    assert pair_cost(0.48, 0.48) == pytest.approx(0.96)


def test_pair_cost_is_unknown_when_a_leg_is_missing():
    assert pair_cost(0.48, None) is None
    assert pair_cost(None, None) is None


def test_pair_cost_rejects_unparseable_legs():
    assert pair_cost("abc", 0.48) is None


# --- two_sided_mid_with_default (live's historical semantic) --------------

def test_default_variant_always_returns_a_number():
    """Live needs a float today; #171 decides whether that should change."""
    from strategy.book_math import two_sided_mid_with_default

    assert two_sided_mid_with_default(_book(), _book()) == 0.50
    assert two_sided_mid_with_default(_book(0.48, 0.52), _book(0.48, 0.52)) == 0.50


def test_default_variant_keeps_a_zero_quote():
    """`bid or ask or 0.50` discarded a legitimate 0.0 as falsy."""
    from strategy.book_math import two_sided_mid_with_default

    # UP priced at 0.0 with no ask: the leg mid is 0.0, not the 0.50 default.
    got = two_sided_mid_with_default(_book(bb=0.0), _book(0.48, 0.52))
    assert got == pytest.approx((0.0 + (1.0 - 0.50)) / 2.0, abs=1e-4)


def test_default_variant_and_honest_variant_agree_on_healthy_books():
    """They may only diverge where a leg cannot be priced at all."""
    from strategy.book_math import two_sided_mid_with_default

    up, down = _book(0.44, 0.46), _book(0.54, 0.56)
    assert two_sided_mid(up, down) == two_sided_mid_with_default(up, down)


# ---------------------------------------------------------------------------
# resting_bid_filled (issue #226) -- the one fill rule, shared by both engines
# ---------------------------------------------------------------------------

def test_a_print_at_our_price_fills_us():
    """A trade printed within the tick tolerance of our bid took our order."""
    from strategy.book_math import resting_bid_filled

    assert resting_bid_filled(0.47, None, [0.47], tick=0.001) is True
    assert resting_bid_filled(0.47, None, [0.4705], tick=0.001) is True
    assert resting_bid_filled(0.47, None, [0.60, 0.4695], tick=0.001) is True


def test_a_print_away_from_our_price_does_not_fill_us():
    from strategy.book_math import resting_bid_filled

    assert resting_bid_filled(0.47, None, [0.48], tick=0.001) is False
    assert resting_bid_filled(0.47, None, [], tick=0.001) is False


def test_an_ask_fully_through_our_price_fills_us():
    """The print was missed; a crossed book is the evidence it happened."""
    from strategy.book_math import resting_bid_filled

    assert resting_bid_filled(0.47, 0.41, [], tick=0.001) is True
    assert resting_bid_filled(0.47, 0.469, [], tick=0.001) is True


def test_an_ask_merely_touching_our_price_does_not_fill_us():
    """Other bids may sit ahead of ours at an equal price, so a touch is not
    a trade. Deliberately more conservative than the venue."""
    from strategy.book_math import resting_bid_filled

    assert resting_bid_filled(0.47, 0.47, [], tick=0.001) is False
    assert resting_bid_filled(0.47, 0.4695, [], tick=0.001) is False


def test_an_unknown_or_unquotable_side_never_fills():
    from strategy.book_math import resting_bid_filled

    assert resting_bid_filled(None, 0.41, [0.47], tick=0.001) is False
    assert resting_bid_filled(0.47, None, [None], tick=0.001) is False
    assert resting_bid_filled(0.47, "junk", ["junk"], tick=0.001) is False


def test_a_quote_placed_onto_a_standing_ask_is_marketable():
    """Issue #226: the leg chase steps to `min(ask, cap)`, landing ON the ask.
    Such an order matches on arrival -- there is no queue in front of it --
    so the touch rule does not apply to the tick it is placed."""
    from strategy.book_math import resting_bid_filled

    assert resting_bid_filled(0.49, 0.49, [], tick=0.001, newly_placed=True) is True
    assert resting_bid_filled(0.49, 0.48, [], tick=0.001, newly_placed=True) is True
    # Once it is resting, the same touch is no longer a fill.
    assert resting_bid_filled(0.49, 0.49, [], tick=0.001) is False


def test_a_newly_placed_quote_below_the_ask_still_rests():
    from strategy.book_math import resting_bid_filled

    assert resting_bid_filled(0.48, 0.51, [], tick=0.001, newly_placed=True) is False


# --- chase_cap (issue #227) ----------------------------------------------

def test_chase_cap_floors_to_whole_cents():
    """Cent flooring, so `entry + chased` cannot exceed the cap by rounding."""
    from strategy.book_math import chase_cap

    # 0.99 - 0.485 = 0.505, which must floor DOWN to 0.50, not round to 0.51.
    assert chase_cap(0.99, 0.485) == 0.50
    assert chase_cap(0.99, 0.48) == 0.51
    assert chase_cap(0.98, 0.48) == 0.50


def test_chase_cap_never_lets_the_pair_exceed_the_ceiling():
    from strategy.book_math import chase_cap

    for entry in (0.31, 0.42, 0.485, 0.50, 0.517, 0.63):
        assert entry + chase_cap(0.99, entry) <= 0.99 + 1e-9


def test_chase_cap_is_negative_when_the_entry_already_breached_the_cap():
    """A leg bought above the cap leaves nothing to spend; the chase must not raise.

    `min(ask, cap)` is then below any quotable price, which is how both engines
    express "do not chase" without a separate branch.
    """
    from strategy.book_math import chase_cap

    assert chase_cap(0.99, 1.00) == -0.01


def test_chase_cap_of_an_unparseable_entry_is_unknown():
    from strategy.book_math import chase_cap

    assert chase_cap(0.99, None) is None
    assert chase_cap(0.99, "abc") is None
    assert chase_cap("abc", 0.48) is None
