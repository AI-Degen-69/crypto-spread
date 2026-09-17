"""Unit tests for Issue #229 (superseding #48 / #96): the dead zone governs the end of the window.

The 10% entry timeout, the naked-leg timeout and the late-start clock are all
deleted; one rule now owns the tail of the window. Tests cover:

1. LiveTrader cancels resting entry orders on entering the dead zone (0 legs filled).
2. LiveTrader skips a window whose first observed tick lands inside the dead zone.
3. LiveTrader keeps quoting the whole window when the dead zone is disabled (0.0).
4. LiveTrader rollover re-arms the dead-zone latch for the next window.
5. Backtest skips a window whose first snapshot lands in the dead zone.
6. Backtest cancels unfilled quotes on entering the dead zone.
7. Backtest under "close": an unpaired leg exits at book bid in the dead zone.
8. Backtest under "hold": an unpaired leg carries to settlement.
9. Both engines validate and default the dead-zone knobs identically.
"""

import time
from unittest.mock import MagicMock
import pytest

from strategy.live_trader import LiveTraderEngine
from backtest import BacktestParams
from backtest.engine import _simulate_window


UP_TOKEN = "0xAAAA_up_token"
DN_TOKEN = "0xBBBB_dn_token"
CID = "0xCID_TIMEOUT"
SLUG = "btc-updown-5m-1788500000"
SERIES = "btc-up-or-down-5m"
DUR = 300

# 5m window at start_ts=1000.0; dead zone 10% -> untradeable from t=1270.0.
START = 1000.0
DEAD_ZONE_AT = 1270.0


def _make_snap(ts: float, mid: float = 0.50, up_ask: float = 0.49, down_ask: float = 0.49,
               start_ts: float = START, tape: list | None = None,
               down_bid: float | None = None, up_bid: float | None = None) -> dict:
    # Both legs are pinned so the *two-sided* mid is `mid` too: UP at `mid`,
    # DOWN at its complement. Centring DOWN on `down_ask` instead described a
    # book no real binary pair produces, and nothing noticed while the engine
    # anchored off the one-sided `s["mid"]` (issue #225).
    half = 0.005
    ba_up = round(mid + half, 4) if up_ask is None else up_ask
    bb_up = round(2.0 * mid - ba_up, 4) if up_bid is None else up_bid
    # `down_bid` opts out of the pinning, for the tests whose whole subject is a
    # leg-imbalanced book.
    bb_dn = round(2.0 * (1.0 - mid) - down_ask, 4) if down_bid is None else down_bid
    return {
        "ts": ts,
        "iso": "2026-09-03T12:00:00+00:00",
        "series": SERIES,
        "duration": DUR,
        "label": "BTC 5m",
        "cid": CID,
        "slug": SLUG,
        "start_ts": start_ts,
        "end_ts": start_ts + DUR,
        "t_rem": max(0.0, (start_ts + DUR) - ts),
        "up_token": UP_TOKEN,
        "down_token": DN_TOKEN,
        "up_book": {
            "token_id": UP_TOKEN,
            "bids": {},
            "asks": {},
            "best_bid": bb_up,
            "best_ask": ba_up,
            "malformed": 0,
        },
        "down_book": {
            "token_id": DN_TOKEN,
            "bids": {},
            "asks": {},
            "best_bid": bb_dn,
            "best_ask": down_ask,
            "malformed": 0,
        },
        "tape_delta": tape or [],
        "mid": mid,
        "touch_pair": (up_ask or 0.5) + (down_ask or 0.5),
        "resting_pair": 0.96,
        "queue_up": 0.0,
        "queue_down": 0.0,
        "err": None,
    }


# ============================================================================
# LIVE TRADER TESTS
# ============================================================================

def _live_engine(**kwargs):
    """LiveTraderEngine with the dead-zone default and CLOB calls mocked out."""
    engine = LiveTraderEngine(load_persisted=False, **kwargs)
    engine.mode = "live"
    engine.is_running = True
    engine.get_clob_client = MagicMock(return_value=None)
    engine.place_live_quote = MagicMock(
        side_effect=lambda token_id, price, size, side: {
            "order_id": f"ord_{token_id}_{side}", "status": "RESTING"}
    )
    engine.cancel_live_order = MagicMock(return_value=True)
    return engine


def _poll(start_ts, cid="0xwin_a", slug="btc-updown-a", mid=0.50):
    """poll_data whose two-sided books yield `mid` in the engine's mid formula."""
    half_spread = 0.01
    up_mid = mid
    down_mid = 1.0 - mid
    return {
        "market": {
            "conditionId": cid,
            "slug": slug,
            "up_token": f"tok_up_{cid}",
            "down_token": f"tok_dn_{cid}",
            "start_ts": start_ts,
            "end_ts": start_ts + 300.0,
        },
        "up_book": {"best_bid": round(up_mid - half_spread, 4), "best_ask": round(up_mid + half_spread, 4)},
        "down_book": {"best_bid": round(down_mid - half_spread, 4), "best_ask": round(down_mid + half_spread, 4)},
    }


def test_live_trader_cancels_unfilled_entry_on_entering_dead_zone():
    """0 legs filled, remaining time crosses the cutoff -> cancel both resting orders."""
    engine = _live_engine(dead_zone_val=0.10)
    slug = "btc-up-or-down-5m"

    # Tick 1 at t=1005s (remaining 295s > 30s cutoff) -> orders placed and resting.
    engine._update_market_strategy(slug, _poll(START), now=1005.0)
    mstate = engine.markets[slug]
    assert mstate.order_id_up is not None
    assert mstate.order_id_down is not None
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"
    assert mstate.entry_cancelled_timeout is False

    # Tick 2 at t=1275s (remaining 25s <= 30s cutoff) -> cancel both orders.
    engine._update_market_strategy(slug, _poll(START), now=1275.0)
    assert mstate.entry_cancelled_timeout is True
    assert mstate.order_status_up == "CANCELLED"
    assert mstate.order_status_down == "CANCELLED"
    assert mstate.status == "DEAD_ZONE_NO_FILL"
    assert "Dead zone" in mstate.last_action
    assert engine.cancel_live_order.call_count == 2


def test_live_trader_failed_cancel_retains_resting_and_retries():
    """If cancel_live_order fails, order stays RESTING and retries on next tick."""
    engine = _live_engine(dead_zone_val=0.10)
    slug = "btc-up-or-down-5m"
    cancel_attempts = []
    engine.cancel_live_order = MagicMock(
        side_effect=lambda oid: (cancel_attempts.append(oid), False)[1])

    engine._update_market_strategy(slug, _poll(START, cid="0xr", slug="btc-r"), now=1005.0)
    mstate = engine.markets[slug]
    assert mstate.order_status_up == "RESTING"

    # Tick 2 crosses the cutoff -> cancel fails.
    engine._update_market_strategy(slug, _poll(START, cid="0xr", slug="btc-r"), now=1275.0)
    assert mstate.entry_cancelled_timeout is False
    assert mstate.order_status_up == "RESTING"
    assert len(cancel_attempts) == 2

    # Now make cancel succeed -> retries and succeeds on the next tick.
    engine.cancel_live_order = MagicMock(return_value=True)
    engine._update_market_strategy(slug, _poll(START, cid="0xr", slug="btc-r"), now=1276.0)
    assert mstate.entry_cancelled_timeout is True
    assert mstate.order_status_up == "CANCELLED"
    assert mstate.status == "DEAD_ZONE_NO_FILL"


def test_live_trader_first_tick_in_dead_zone_skips_window():
    """A window the engine joined inside the dead zone is never opened."""
    engine = _live_engine(dead_zone_val=0.10)
    slug = "btc-up-or-down-5m"

    # First tick 275s into a 300s window — inside the dead zone.
    engine._update_market_strategy(slug, _poll(START), now=1275.0)

    mstate = engine.markets[slug]
    assert mstate.late_start_skip is True
    assert engine.place_live_quote.call_count == 0
    assert mstate.order_id_up is None
    assert mstate.order_id_down is None
    assert mstate.status == "LATE_START_SKIPPED"
    assert "waiting for next window" in mstate.last_action


def test_live_trader_disabled_dead_zone_quotes_the_whole_window():
    """dead_zone_val=0.0 disables the rule: orders keep resting deep into the window."""
    engine = _live_engine(dead_zone_val=0.0)
    slug = "btc-up-or-down-5m"

    engine._update_market_strategy(slug, _poll(START), now=1005.0)
    mstate = engine.markets[slug]
    assert mstate.order_status_up == "RESTING"

    # 280s in — far past where the 10% zone would have cancelled.
    engine._update_market_strategy(slug, _poll(START), now=1280.0)
    assert mstate.entry_cancelled_timeout is False
    assert mstate.late_start_skip is False
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"
    assert engine.cancel_live_order.call_count == 0


def test_live_trader_dead_zone_cancel_leaves_a_filled_leg_open_until_its_own_rule():
    """One leg filled before the cutoff: the dead zone cancels the *opposite*
    unfilled order, and the filled leg is governed by naked_leg_at_expiry —
    not by the entry-cancel path."""
    engine = _live_engine(dead_zone_val=0.10)
    engine.enable_leg_chase = False
    engine.mode = "paper"
    engine.is_running = True
    slug = "btc-up-or-down-5m"

    # Open quotes at the 50/50 mid (resting bids 0.48/0.48).
    engine._update_market_strategy(slug, _poll(START), now=1005.0)

    # Tick 2: UP ask drops to 0.479 -> UP fills before the cutoff.
    poll_fill = {
        "market": _poll(START)["market"],
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.49, "best_ask": 0.52},
    }
    engine._update_market_strategy(slug, poll_fill, now=1010.0)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.entry_cancelled_timeout is False

    # Tick 3 crosses into the dead zone: the naked leg rule owns the window
    # from here — under the default "close" the unpaired UP exits at book bid
    # instead of the entry-cancel flag firing.
    poll_naked = {
        "market": _poll(START)["market"],
        "up_book": {"best_bid": 0.46, "best_ask": 0.49},
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    engine._update_market_strategy(slug, poll_naked, now=1275.0)
    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    assert engine.trades[-1].action == "STOP_EXIT_UP"


def test_live_trader_rollover_rearms_the_dead_zone_latch():
    """After window rollover, a fresh window quotes even if the old one was skipped."""
    engine = _live_engine(dead_zone_val=0.10)
    slug = "btc-up-or-down-5m"

    # Window 1 first tick inside the dead zone -> skipped.
    engine._update_market_strategy(slug, _poll(START, cid="0xwin1", slug="btc-01"), now=1275.0)
    mstate = engine.markets[slug]
    assert mstate.late_start_skip is True

    # Rollover to window 2 at t=1305s (5s into the new window) -> quotes normally.
    engine._update_market_strategy(
        slug, _poll(1300.0, cid="0xwin2", slug="btc-02"), now=1305.0)
    assert mstate.late_start_skip is False
    assert mstate.entry_cancelled_timeout is False
    assert mstate.condition_id == "0xwin2"
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"


def test_live_trader_shifting_start_ts_does_not_re_arm_a_quoted_window():
    """A market loader with an unstable start_ts must not turn a live window late."""
    engine = _live_engine(dead_zone_val=0.0)
    slug = "btc-up-or-down-5m"

    engine._update_market_strategy(slug, _poll(START), now=1005.0)
    mstate = engine.markets[slug]
    assert mstate.order_status_up == "RESTING"

    # Same window, same condition id, but start_ts drifts and 200s have passed.
    engine._update_market_strategy(slug, _poll(1000.5), now=1205.0)

    assert mstate.late_start_skip is False
    assert mstate.entry_cancelled_timeout is False
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"


# ============================================================================
# BACKTEST REPLAY ENGINE TESTS
# ============================================================================

def test_backtest_first_tick_in_dead_zone_skips_window():
    """A window whose first snapshot lands in the dead zone is not entered."""
    snaps = [
        _make_snap(1275.0, mid=0.48, up_ask=0.48, down_ask=0.52,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
    ]
    res = _simulate_window(snaps, BacktestParams(offset=0.02, dead_zone_val=0.10))
    assert res.entered is False
    assert res.filled_up is False
    assert res.filled_down is False
    assert res.pair_captured is False


def test_backtest_cancels_unfilled_at_dead_zone_entry():
    """Prints that only cross the resting price inside the dead zone are not filled."""
    snaps = [
        _make_snap(1005.0, mid=0.50, up_ask=0.51, down_ask=0.51),
        _make_snap(1015.0, mid=0.50, up_ask=0.51, down_ask=0.51),
        _make_snap(1025.0, mid=0.50, up_ask=0.51, down_ask=0.51),
        # At 1280s (inside the dead zone), price crosses to 0.48.
        _make_snap(1280.0, mid=0.48, up_ask=0.48, down_ask=0.54,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
    ]
    res = _simulate_window(snaps, BacktestParams(offset=0.02, dead_zone_val=0.10))
    assert res.filled_up is False
    assert res.filled_down is False
    assert res.pair_captured is False

    # With the dead zone disabled the same print fills.
    res_off = _simulate_window(
        snaps, BacktestParams(offset=0.02, dead_zone_val=0.0))
    assert res_off.filled_up is True


def test_backtest_unpaired_leg_closes_in_dead_zone_by_default():
    """Default naked_leg_at_expiry="close": the unpaired leg exits at book bid."""
    snaps = [
        _make_snap(1005.0, mid=0.50, up_ask=0.48, down_ask=0.52,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
        _make_snap(1275.0, mid=0.50, up_bid=0.46, up_ask=0.48, down_ask=0.55),
    ]
    res = _simulate_window(
        snaps, BacktestParams(offset=0.02, dead_zone_val=0.10, naked_leg_at_expiry="close"))
    assert res.filled_up is True
    assert res.filled_down is False
    assert res.exit_taken is True
    assert res.exit_side == "up"
    assert res.exit_price == 0.46


def test_backtest_unpaired_leg_holds_in_dead_zone_under_hold():
    """naked_leg_at_expiry="hold": the unpaired leg carries to settlement."""
    snaps = [
        _make_snap(1005.0, mid=0.50, up_ask=0.48, down_ask=0.52,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
        _make_snap(1275.0, mid=0.50, up_bid=0.46, up_ask=0.48, down_ask=0.55),
    ]
    res = _simulate_window(
        snaps, BacktestParams(offset=0.02, dead_zone_val=0.10, naked_leg_at_expiry="hold"))
    assert res.filled_up is True
    assert res.filled_down is False
    assert res.exit_taken is False


def test_backtest_sec_unit_matches_pct_semantics():
    """30 seconds absolute on a 300s window behaves like 10 percent."""
    early_then_print = [
        _make_snap(1005.0, mid=0.50, up_ask=0.51, down_ask=0.51),
        _make_snap(1280.0, mid=0.48, up_ask=0.48, down_ask=0.54,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
    ]
    res_pct = _simulate_window(
        early_then_print, BacktestParams(offset=0.02, dead_zone_val=0.10, dead_zone_unit="pct"))
    res_sec = _simulate_window(
        early_then_print, BacktestParams(offset=0.02, dead_zone_val=30.0, dead_zone_unit="sec"))
    assert res_pct.filled_up is False
    assert res_sec.filled_up is False
    assert res_pct.entered == res_sec.entered


# ============================================================================
# Entry controls: a balanced open fills, a one-sided open holds without a latch
# (Issue #228: the adverse-open gate these controlled for is deleted)
# ============================================================================


def test_backtest_enters_window_with_balanced_open():
    """A mid at 0.50 — inside the quotable range — still fills normally."""
    snaps = [
        _make_snap(1005.0, mid=0.50, up_ask=0.505, down_ask=0.505,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
    ]
    res = _simulate_window(snaps, BacktestParams(offset=0.02))
    assert res.filled_up is True


def test_backtest_one_sided_open_book_does_not_cancel_entry():
    """A one-sided book at open holds quoting without latching a cancel.

    It is not quoted either (issue #225): with one leg unpriceable there is no
    two-sided mid and so no anchor. What must not happen is a *latched* cancel
    -- the next tick that prices both legs still enters.
    """
    one_sided = _make_snap(1005.0, mid=0.50, up_ask=0.505, down_ask=0.505,
                           tape=[{"asset": UP_TOKEN, "price": 0.48}])
    # Strip the DOWN ask so the opening mid cannot be evaluated on this tick.
    one_sided["down_book"]["best_ask"] = None
    p = BacktestParams(offset=0.02)

    assert _simulate_window([one_sided], p).filled_up is False, (
        "a book that priced one leg was quoted anyway")

    priced = _make_snap(1010.0, mid=0.50, up_ask=0.505, down_ask=0.505,
                        tape=[{"asset": UP_TOKEN, "price": 0.48}])
    assert _simulate_window([one_sided, priced], p).filled_up is True, (
        "the one-sided open latched a cancel the drift gate never evaluated")


# ============================================================================
# VALIDATION — the dead-zone knobs carry the invariants the deleted clocks had
# ============================================================================

def test_backtest_rejects_out_of_range_dead_zone_val():
    with pytest.raises(ValueError):
        BacktestParams(dead_zone_val=1.5)  # pct unit: fraction of window
    with pytest.raises(ValueError):
        BacktestParams(dead_zone_val=-0.01)
    with pytest.raises(ValueError):
        BacktestParams(dead_zone_val=30.0, dead_zone_unit="min")


def test_backtest_rejects_unknown_naked_leg_at_expiry():
    with pytest.raises(ValueError):
        BacktestParams(naked_leg_at_expiry="keep")


def test_dead_zone_defaults_match_across_engines():
    """The two engines must ship the same dead-zone definition."""
    bt = BacktestParams()
    lt = LiveTraderEngine(load_persisted=False)
    assert bt.dead_zone_val == lt.dead_zone_val
    assert bt.dead_zone_unit == lt.dead_zone_unit
    assert bt.naked_leg_at_expiry == lt.naked_leg_at_expiry


# ============================================================================
# No re-entry without the mechanism (issue #228): a dead-zone-cancelled window
# stays cancelled even when the mid is healthy. The re-entry tests that stood
# here were removed with the behaviour.
# ============================================================================

# The balanced book puts the mid at 0.50 with asks above the 0.48 resting
# price so fills come only from the tape.
_BALANCED_UP = 0.505
_BALANCED_DN = 0.505


def _fill_tape():
    """Tape prints at the 0.48 resting price on both legs (offset 0.02)."""
    return [{"asset": UP_TOKEN, "price": 0.48},
            {"asset": DN_TOKEN, "price": 0.48}]


def test_backtest_dead_zone_cancelled_window_never_reenters_even_at_mid_050():
    """A window cancelled by the dead zone stays cancelled at a healthy mid."""
    snaps = [
        _make_snap(1005.0, mid=0.50, up_ask=_BALANCED_UP, down_ask=_BALANCED_DN),
        # Inside the dead zone, with the mid still healthy and a fill print waiting.
        _make_snap(1280.0, mid=0.50, up_ask=_BALANCED_UP, down_ask=_BALANCED_DN,
                   tape=_fill_tape()),
    ]
    res = _simulate_window(snaps, BacktestParams(offset=0.02, dead_zone_val=0.10))
    assert res.filled_up is False
    assert res.filled_down is False
    assert res.pair_captured is False
    assert res.reentry_count == 0


def test_backtest_late_first_tick_is_never_reentered():
    """A window skipped for a first tick inside the dead zone stays skipped."""
    snaps = [
        # First snapshot lands 280s into a 300s window: inside the dead zone.
        _make_snap(1280.0, mid=0.50, up_ask=_BALANCED_UP, down_ask=_BALANCED_DN),
        _make_snap(1285.0, mid=0.50, up_ask=_BALANCED_UP, down_ask=_BALANCED_DN,
                   tape=_fill_tape()),
    ]
    res = _simulate_window(snaps, BacktestParams(offset=0.02, dead_zone_val=0.10))
    assert res.reentry_count == 0
    assert res.filled_up is False
