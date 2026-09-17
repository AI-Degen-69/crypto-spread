"""Canonical parity test harness between backtest/engine.py and strategy/live_trader.py (Issue #214).

Enforces behavioral identity across the entire decision-visible surface:
`entered`, `filled_up`, `filled_down`, `entry_price_up`, `entry_price_down`,
`pair_captured`, `exit_taken`, `exit_side`, `chased_leg`, `pairs_count`, `stops_count`.

Fast synthetic execution (<5s total).
"""
from __future__ import annotations

import pytest
from typing import Any, Dict, List, Tuple

from backtest.engine import BacktestParams, _simulate_window
from strategy.live_trader import LiveTraderEngine
from strategy.markets import LiveMarket


UP_TOKEN = "0xTOKEN_UP_PARITY"
DN_TOKEN = "0xTOKEN_DN_PARITY"
CID = "0xCID_ENGINE_PARITY"
SLUG = "btc-up-or-down-5m"
SERIES = "btc-up-or-down-5m"
START_TS = 1_770_000_000.0
DURATION = 300.0

SURFACE_KEYS = [
    "entered",
    "filled_up",
    "filled_down",
    "entry_price_up",
    "entry_price_down",
    "pair_captured",
    "exit_taken",
    "exit_side",
    "chased_leg",
    "pairs_count",
    "stops_count",
]


def make_snap(
    offset_sec: float,
    up_bid: float | None,
    up_ask: float | None,
    dn_bid: float | None,
    dn_ask: float | None,
    recorded_mid: float | None = None,
    tape: list[dict] | None = None,
    start_ts: float = START_TS,
    duration: float = DURATION,
    slug: str = SLUG,
    cid: str = CID,
    up_token: str = UP_TOKEN,
    dn_token: str = DN_TOKEN,
) -> dict:
    """Create a single tick dictionary compatible with both engines."""
    ts = start_ts + offset_sec
    t_rem = max(0.0, (start_ts + duration) - ts)
    return {
        "ts": ts,
        "iso": "2026-08-29T00:00:00+00:00",
        "series": slug,
        "slug": slug,
        "cid": cid,
        "duration": int(duration),
        "label": "BTC 5m" if duration == 300 else "BTC 15m",
        "start_ts": start_ts,
        "end_ts": start_ts + duration,
        "t_rem": t_rem,
        "up_token": up_token,
        "down_token": dn_token,
        "up_book": {
            "token_id": up_token,
            "bids": {float(up_bid): 100.0} if up_bid is not None else {},
            "asks": {float(up_ask): 100.0} if up_ask is not None else {},
            "best_bid": up_bid,
            "best_ask": up_ask,
            "malformed": 0,
        },
        "down_book": {
            "token_id": dn_token,
            "bids": {float(dn_bid): 100.0} if dn_bid is not None else {},
            "asks": {float(dn_ask): 100.0} if dn_ask is not None else {},
            "best_bid": dn_bid,
            "best_ask": dn_ask,
            "malformed": 0,
        },
        "tape_delta": tape or [],
        "mid": recorded_mid,
        "touch_pair": round((up_ask or 0.0) + (dn_ask or 0.0), 4),
        "resting_pair": 0.96,
        "queue_up": 0.0,
        "queue_down": 0.0,
        "err": None,
    }


def snaps_to_polls(snaps: list[dict]) -> list[tuple[float, dict]]:
    """Convert backtest snaps to (now, poll_data) tuples for LiveTraderEngine._update_market_strategy."""
    polls: list[tuple[float, dict]] = []
    for s in snaps:
        now_ts = float(s["ts"])
        market = LiveMarket(
            condition_id=s.get("cid", CID),
            market_slug=s.get("slug", SLUG),
            up_token=s.get("up_token", UP_TOKEN),
            down_token=s.get("down_token", DN_TOKEN),
            start_ts=float(s.get("start_ts", START_TS)),
            end_ts=float(s.get("end_ts", START_TS + s.get("duration", DURATION))),
            tick_size=0.01,
            neg_risk=False,
        )
        poll_data = {
            "market": market,
            "up_book": dict(s["up_book"]),
            "down_book": dict(s["down_book"]),
        }
        polls.append((now_ts, poll_data))
    return polls


def live_outcome(snaps: list[dict], params: BacktestParams) -> dict:
    """Run the live decision path headlessly in paper mode; return the SPEC §2 surface."""
    slug = snaps[0].get("slug", SLUG)
    dur = int(snaps[0].get("duration", DURATION))
    exit_th = params.exit_thresh(slug, dur)

    engine = LiveTraderEngine(
        load_persisted=False,
        selected_markets=[slug],
        dead_zone_val=params.dead_zone_val,
        dead_zone_unit=params.dead_zone_unit,
        naked_leg_at_expiry=params.naked_leg_at_expiry,
    )
    engine.mode = "paper"
    engine.is_running = True

    # Disable async background and network side-effects
    engine.stream_bridge.start = lambda *a, **k: None
    engine.stream_bridge.update_market_tokens = lambda *a, **k: None
    engine._schedule_wallet_balance_fetch = lambda *a, **k: None
    engine.ensure_telemetry_streaming = lambda *a, **k: None
    engine._record_fill_telemetry = lambda *a, **k: None
    engine.fill_telemetry_async = False

    # Mirror BacktestParams configuration
    engine.offset = params.offset
    engine.enable_leg_chase = params.enable_leg_chase
    engine.max_pair_cost = params.max_pair_cost
    engine.entry_delay_sec = params.entry_delay_sec
    engine.quote_range = params.quote_range
    engine.exit_reversal = params.exit_reversal
    engine.exit_thresh = exit_th
    engine.shares = params.quote_shares
    engine.quoting_halted = False
    engine.is_running = True

    ever_entered = False
    for now_ts, poll_data in snaps_to_polls(snaps):
        engine._update_market_strategy(slug, poll_data, now=now_ts)
        mstate = engine.markets[slug]
        if (
            mstate.order_status_up == "RESTING"
            or mstate.order_status_down == "RESTING"
            or mstate.order_id_up is not None
            or mstate.order_id_down is not None
            or mstate.filled_up
            or mstate.filled_down
            or mstate.pairs_count > 0
            or mstate.stops_count > 0
        ):
            ever_entered = True

    mstate = engine.markets[slug]
    exit_side = (mstate.exit_side or "").lower()
    chased_leg = (mstate.chased_leg or "").lower()

    return {
        "entered": ever_entered,
        "filled_up": bool(mstate.filled_up),
        "filled_down": bool(mstate.filled_down),
        "entry_price_up": mstate.fill_price_up,
        "entry_price_down": mstate.fill_price_down,
        "pair_captured": bool(mstate.pair_captured),
        "exit_taken": bool(mstate.exit_taken),
        "exit_side": exit_side,
        "chased_leg": chased_leg,
        "pairs_count": int(mstate.pairs_count),
        "stops_count": int(mstate.stops_count),
    }


def backtest_outcome(snaps: list[dict], params: BacktestParams) -> dict:
    """Run pure _simulate_window; return the identical SPEC §2 surface."""
    res = _simulate_window(snaps, params)
    return {
        "entered": bool(res.entered),
        "filled_up": bool(res.filled_up),
        "filled_down": bool(res.filled_down),
        "entry_price_up": res.entry_price_up,
        "entry_price_down": res.entry_price_down,
        "pair_captured": bool(res.pair_captured),
        "exit_taken": bool(res.exit_taken),
        "exit_side": (res.exit_side or "").lower(),
        "chased_leg": (res.chased_leg or "").lower(),
        "pairs_count": int(res.pairs_count),
        "stops_count": int(res.stops_count),
    }


def assert_parity(snaps: list[dict], params: BacktestParams, context: str = "") -> None:
    """Compare live and backtest outcomes; raise AssertionError with readable diff on divergence."""
    live = live_outcome(snaps, params)
    bt = backtest_outcome(snaps, params)

    diffs = []
    for k in SURFACE_KEYS:
        v_live = live.get(k)
        v_bt = bt.get(k)
        if isinstance(v_live, float) and isinstance(v_bt, float):
            if abs(v_live - v_bt) >= 1e-4:
                diffs.append((k, v_live, v_bt))
        elif v_live != v_bt:
            diffs.append((k, v_live, v_bt))

    if not diffs:
        return

    # Diagnose the earliest tick where divergence occurred
    earliest_diverging_tick = None
    first_diff_key = diffs[0][0]
    for i in range(1, len(snaps) + 1):
        sub_live = live_outcome(snaps[:i], params)
        sub_bt = backtest_outcome(snaps[:i], params)
        sub_l_val = sub_live.get(first_diff_key)
        sub_b_val = sub_bt.get(first_diff_key)
        mismatch = False
        if isinstance(sub_l_val, float) and isinstance(sub_b_val, float):
            mismatch = (abs(sub_l_val - sub_b_val) >= 1e-4)
        else:
            mismatch = (sub_l_val != sub_b_val)
        if mismatch:
            earliest_diverging_tick = (i - 1, snaps[i - 1])
            break

    lines = ["Parity Divergence Detected:"]
    if context:
        lines.append(f"Context: {context}")
    for k, v_l, v_b in diffs:
        lines.append(f"  - Field '{k}': Live={v_l!r} != Backtest={v_b!r}")
    if earliest_diverging_tick is not None:
        idx, tick_snap = earliest_diverging_tick
        lines.append(
            f"  Earliest divergence at tick index #{idx} "
            f"(ts={tick_snap.get('ts')}, t_rem={tick_snap.get('t_rem')}, mid={tick_snap.get('mid')})"
        )
    raise AssertionError("\n".join(lines))


# ── Tests: Harness Plumbing & Diagnostics ──────────────────────────────────────


def test_adapter_snaps_to_polls():
    """Verify that snaps_to_polls maps data faithfully to LiveMarket and books."""
    snaps = [
        make_snap(0.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50),
        make_snap(1.0, 0.48, 0.50, 0.50, 0.52, recorded_mid=0.49),
    ]
    polls = snaps_to_polls(snaps)
    assert len(polls) == 2

    ts0, poll0 = polls[0]
    assert ts0 == START_TS
    assert isinstance(poll0["market"], LiveMarket)
    assert poll0["market"].condition_id == CID
    assert poll0["up_book"]["best_bid"] == 0.49
    assert poll0["down_book"]["best_ask"] == 0.51


def test_outcome_surface_keys():
    """Verify both engines populate the exact declared surface keys."""
    snaps = [make_snap(0.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50)]
    params = BacktestParams()
    live = live_outcome(snaps, params)
    bt = backtest_outcome(snaps, params)

    assert set(live.keys()) == set(SURFACE_KEYS)
    assert set(bt.keys()) == set(SURFACE_KEYS)


def test_assert_parity_formatting_on_mismatch(monkeypatch):
    """Verify assert_parity produces a structured, informative error message on mismatch."""
    snaps = [
        make_snap(0.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50),
    ]
    params = BacktestParams()
    import tests.test_engine_parity as mod
    orig_live = mod.live_outcome
    monkeypatch.setattr(mod, "live_outcome", lambda s, p: {**orig_live(s, p), "pair_captured": True})

    with pytest.raises(AssertionError) as exc_info:
        assert_parity(snaps, params, context="Synthetic Diff Test")

    msg = str(exc_info.value)
    assert "Parity Divergence Detected:" in msg
    assert "Synthetic Diff Test" in msg
    assert "Field 'pair_captured': Live=True != Backtest=False" in msg


# ── Tests: Seed Parity Scenarios ───────────────────────────────────────────────


def test_seed_balanced_open_to_pair_merge():
    """Scenario 1: Standard balanced open; both legs fill; pair merges."""
    ticks = [
        # Tick 0: Mid 0.50 -> Resting bids at 0.48 / 0.48
        make_snap(10.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50),
        # Tick 1: UP ask touches 0.479 <= 0.48 -> UP fills at 0.48
        make_snap(11.0, 0.47, 0.479, 0.51, 0.52, recorded_mid=0.48),
        # Tick 2: DN ask touches 0.479 <= 0.48 -> DN fills at 0.48 -> Pair Merges!
        make_snap(12.0, 0.51, 0.52, 0.47, 0.479, recorded_mid=0.52),
    ]
    params = BacktestParams(offset=0.02, dead_zone_val=0.0, enable_leg_chase=False)
    assert_parity(ticks, params, context="Balanced open to pair merge")

    # Explicit checks on the merged outcome
    bt = backtest_outcome(ticks, params)
    assert bt["pair_captured"] is True
    assert bt["pairs_count"] == 1
    assert bt["stops_count"] == 0
    assert bt["entry_price_up"] == pytest.approx(0.48)
    assert bt["entry_price_down"] == pytest.approx(0.48)


def test_seed_real_mid_anchor():
    """Scenario 2: Opening quote anchored to real mid, not hardcoded 0.50 (#206)."""
    # Mid 0.53 -> With offset 0.02, quotes rest at 0.51 (UP) and 0.45 (DN)
    ticks = [
        make_snap(10.0, 0.525, 0.535, 0.465, 0.475, recorded_mid=0.53),
        # Ask touches UP quote at 0.51 -> fills at 0.51
        make_snap(11.0, 0.50, 0.509, 0.48, 0.49, recorded_mid=0.51),
    ]
    params = BacktestParams(offset=0.02, dead_zone_val=0.0, enable_leg_chase=False)
    assert_parity(ticks, params, context="Real mid anchor")

    bt = backtest_outcome(ticks, params)
    assert bt["entered"] is True
    assert bt["filled_up"] is True
    assert bt["entry_price_up"] == pytest.approx(0.51)


def test_seed_pair_cost_cap_does_not_block_quoting():
    """Scenario 3: max_pair_cost does not block quoting even when touch cost > cap (#204)."""
    # Touch cost is 0.55 + 0.55 = 1.10 > max_pair_cost (0.99)
    ticks = [
        make_snap(10.0, 0.45, 0.55, 0.45, 0.55, recorded_mid=0.50),
    ]
    params = BacktestParams(offset=0.02, max_pair_cost=0.99, dead_zone_val=0.0)
    assert_parity(ticks, params, context="Pair cost cap non-blocking")

    bt = backtest_outcome(ticks, params)
    assert bt["entered"] is True


def test_seed_unpriceable_leg_skips_entry():
    """Scenario 4: Unpriceable leg (no two-sided book) skips entry (#207)."""
    # UP book has no bids or asks
    ticks = [
        make_snap(10.0, None, None, 0.49, 0.51, recorded_mid=0.50),
    ]
    params = BacktestParams(offset=0.02, dead_zone_val=0.0)
    assert_parity(ticks, params, context="Unpriceable leg")

    bt = backtest_outcome(ticks, params)
    assert bt["entered"] is False
    assert bt["filled_up"] is False
    assert bt["filled_down"] is False


def test_seed_stop_loss_anchored_to_entry_price():
    """Scenario 5: Stop loss anchored to fill price fires at adverse drift (#209 / #230)."""
    ticks = [
        # Tick 0: Mid 0.50 -> Quotes 0.48 / 0.48
        make_snap(10.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50),
        # Tick 1: UP fills at 0.48
        make_snap(11.0, 0.47, 0.479, 0.51, 0.52, recorded_mid=0.48),
        # Tick 2: Mid moves adversely to 0.42 (delta 0.06 >= 0.05 exit_thresh) -> Stop Exit
        make_snap(12.0, 0.41, 0.43, 0.57, 0.59, recorded_mid=0.42),
    ]
    params = BacktestParams(offset=0.02, exit_thresh_by_slug={"default_5m": 0.05}, dead_zone_val=0.0, enable_leg_chase=False)
    assert_parity(ticks, params, context="Stop loss anchored to entry")

    bt = backtest_outcome(ticks, params)
    assert bt["filled_up"] is True
    assert bt["exit_taken"] is True
    assert bt["exit_side"] == "up"
    assert bt["stops_count"] == 1


def test_seed_multi_round_fresh_start():
    """Scenario 6: Clean market re-enters and captures subsequent pair outside dead zone (#232)."""
    ticks = [
        # Round 1:
        make_snap(10.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50),
        make_snap(11.0, 0.47, 0.479, 0.51, 0.52, recorded_mid=0.48),
        make_snap(12.0, 0.51, 0.52, 0.47, 0.479, recorded_mid=0.52),  # Pair 1 merged!
        # Round 2:
        make_snap(20.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50),  # Fresh start!
        make_snap(21.0, 0.51, 0.52, 0.47, 0.479, recorded_mid=0.52),
        make_snap(22.0, 0.47, 0.479, 0.51, 0.52, recorded_mid=0.48),  # Pair 2 merged!
    ]
    params = BacktestParams(offset=0.02, dead_zone_val=0.10, enable_leg_chase=False)
    assert_parity(ticks, params, context="Multi-round fresh start")

    bt = backtest_outcome(ticks, params)
    assert bt["pairs_count"] == 2
    assert bt["stops_count"] == 0
    assert bt["pair_captured"] is True


# ── Tests: Parameter Variations Matrix (User-Approved Improvement) ─────────────


@pytest.mark.parametrize(
    "params_variation",
    [
        pytest.param(BacktestParams(), id="defaults"),
        pytest.param(BacktestParams(offset=0.01), id="tight_offset_001"),
        pytest.param(BacktestParams(offset=0.04), id="wide_offset_004"),
        pytest.param(BacktestParams(enable_leg_chase=True, max_pair_cost=0.98), id="leg_chase_on"),
        pytest.param(BacktestParams(dead_zone_val=0.20, dead_zone_unit="pct"), id="dead_zone_pct_20"),
        pytest.param(BacktestParams(dead_zone_val=60.0, dead_zone_unit="sec"), id="dead_zone_sec_60"),
        pytest.param(BacktestParams(entry_delay_sec=15.0), id="entry_delay_15s"),
        pytest.param(BacktestParams(quote_range=(0.20, 0.80)), id="narrow_quote_range"),
        pytest.param(BacktestParams(exit_thresh_by_slug={"default_5m": 0.03}), id="tighter_stop_003"),
    ],
)
def test_parity_parameter_matrix(params_variation: BacktestParams):
    """Parameter matrix fixture ensuring all standard knob combinations maintain identical behavior."""
    ticks = [
        make_snap(20.0, 0.49, 0.51, 0.49, 0.51, recorded_mid=0.50),
        make_snap(21.0, 0.47, 0.479, 0.51, 0.52, recorded_mid=0.48),
        make_snap(22.0, 0.51, 0.52, 0.47, 0.479, recorded_mid=0.52),
    ]
    assert_parity(ticks, params_variation, context=f"Param variation {params_variation}")
