"""Issue #225: both engines anchor the entry the same way, over the same book.

Not the full parity harness (#214) -- this drives the two engines over one
shared snapshot sequence and compares the one decision this issue is about:
where the opening quote rests, and when no quote is placed at all.

The harness, when it lands, replaces the plumbing here. The scenarios stay.
"""
from __future__ import annotations

import pytest

from backtest.engine import BacktestParams, _simulate_window
from strategy.live_trader import LiveTraderEngine
from strategy.markets import LiveMarket

SLUG = "btc-up-or-down-5m"
CID = "0x225parity"
UP_TOKEN = "tok_up_225"
DN_TOKEN = "tok_dn_225"
START_TS = 1_770_000_000.0
DURATION = 300.0


def _snap(offset_sec: float, up_bid, up_ask, dn_bid, dn_ask,
          recorded_mid=None, tape=None) -> dict:
    """One tick in the collector's shape, with every book side under control."""
    return {
        "ts": START_TS + offset_sec, "iso": "x", "series": SLUG, "slug": SLUG,
        "cid": CID, "duration": int(DURATION), "label": "BTC 5m",
        "start_ts": START_TS, "end_ts": START_TS + DURATION,
        "t_rem": DURATION - offset_sec,
        "up_token": UP_TOKEN, "down_token": DN_TOKEN,
        "up_book": {"token_id": UP_TOKEN, "bids": {}, "asks": {},
                    "best_bid": up_bid, "best_ask": up_ask, "malformed": 0},
        "down_book": {"token_id": DN_TOKEN, "bids": {}, "asks": {},
                      "best_bid": dn_bid, "best_ask": dn_ask, "malformed": 0},
        "tape_delta": tape or [], "mid": recorded_mid,
        "touch_pair": 0.99, "resting_pair": 0.96,
        "queue_up": 0.0, "queue_down": 0.0, "err": None,
    }


def _live_engine(offset: float, **overrides) -> LiveTraderEngine:
    """A paper engine with every gate off, so one decision is under test.

    `overrides` sets any further attribute afterwards — issue #227's chase-cap
    parity needs the chase on and a cap pinned, which is the only thing that
    differs between its scenarios and the ones here.
    """
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True
    engine.offset = offset
    engine.enable_leg_chase = False
    engine.entry_timeout_pct = None
    engine.max_start_elapsed_pct = 0.0
    engine.entry_band = 0.0
    for name, value in overrides.items():
        setattr(engine, name, value)
    return engine


def _drive_live(snaps: list[dict], offset: float, **overrides):
    """Replay `snaps` through the live engine and return its market state."""
    engine = _live_engine(offset, **overrides)
    market = LiveMarket(
        condition_id=CID, market_slug=SLUG,
        up_token=UP_TOKEN, down_token=DN_TOKEN,
        start_ts=START_TS, end_ts=START_TS + DURATION,
        tick_size=0.01, neg_risk=False,
    )
    mstate = engine.markets[SLUG]
    for s in snaps:
        engine._update_market_strategy(SLUG, {
            "market": market,
            "up_book": dict(s["up_book"]),
            "down_book": dict(s["down_book"]),
        }, s["ts"])
    return engine, mstate


# --- the shared scenario ----------------------------------------------------
#
# Tick 0 prices only the DOWN leg, and carries a recorded `mid` of 0.50 from
# the UP leg it can no longer quote. Tick 1 prices both, at a mid of 0.53 --
# inside the adverse-open gate, so the window is entered rather than skipped
# for a reason that has nothing to do with the anchor.
#
# Anchoring off the recorded `mid` rests the pair at 0.48/0.48 and keeps it
# there. Anchoring off the two-sided mid places nothing on tick 0 and rests at
# 0.51/0.45 on tick 1.
ONE_SIDED_THEN_PRICED = [
    _snap(0.0, None, None, 0.495, 0.505, recorded_mid=0.50),
    _snap(20.0, 0.525, 0.535, 0.465, 0.475, recorded_mid=0.53),
    _snap(40.0, 0.525, 0.535, 0.465, 0.475, recorded_mid=0.53),
]


def test_neither_engine_quotes_a_book_it_cannot_price():
    """Tick 0 prices one leg. Neither engine rests anything on it."""
    _engine, mstate = _drive_live(ONE_SIDED_THEN_PRICED[:1], offset=0.02)
    assert mstate.order_id_up is None and mstate.order_id_down is None
    assert not mstate.filled_up and not mstate.filled_down

    w = _simulate_window(ONE_SIDED_THEN_PRICED[:1],
                         BacktestParams(offset=0.02, entry_timeout_pct=0.0,
                                        max_start_elapsed_pct=0.0, entry_band=0.0))
    assert not w.filled_up and not w.filled_down
    assert w.entered is False


def test_both_engines_anchor_at_the_mid_of_the_tick_that_places():
    """The quote rests at 0.51/0.45 in both, not at the stale 0.48/0.48."""
    _engine, mstate = _drive_live(ONE_SIDED_THEN_PRICED, offset=0.02)
    assert mstate.resting_up == pytest.approx(0.51)
    assert mstate.resting_down == pytest.approx(0.45)

    # The backtest is driven with a tape print at the live engine's resting
    # price. It fills only if the two engines put the quote in the same place,
    # which is the assertion -- no expected number is written into the
    # backtest's side of the comparison.
    snaps = [dict(s) for s in ONE_SIDED_THEN_PRICED]
    snaps[2] = {**snaps[2], "tape_delta": [
        {"asset": UP_TOKEN, "price": mstate.resting_up, "size": 10.0},
        {"asset": DN_TOKEN, "price": mstate.resting_down, "size": 10.0},
    ]}
    w = _simulate_window(snaps, BacktestParams(offset=0.02, entry_timeout_pct=0.0,
                                               max_start_elapsed_pct=0.0,
                                               entry_band=0.0))
    assert w.filled_up and w.filled_down
    assert w.entry_price_up == pytest.approx(mstate.resting_up)
    assert w.entry_price_down == pytest.approx(mstate.resting_down)


def test_the_backtest_no_longer_rests_where_the_recorded_mid_pointed():
    """The old anchor's price, driven through the same window, does not fill.

    Without this the test above would pass for a backtest that simply never
    fills. 0.48/0.48 is where `s["mid"] = 0.50` used to put the quote.
    """
    snaps = [dict(s) for s in ONE_SIDED_THEN_PRICED]
    snaps[2] = {**snaps[2], "tape_delta": [
        {"asset": UP_TOKEN, "price": 0.48, "size": 10.0},
        {"asset": DN_TOKEN, "price": 0.48, "size": 10.0},
    ]}
    w = _simulate_window(snaps, BacktestParams(offset=0.02, entry_timeout_pct=0.0,
                                               max_start_elapsed_pct=0.0,
                                               entry_band=0.0))
    assert not w.filled_up and not w.filled_down
