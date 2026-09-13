"""Tests for the SPREAD-2 backtest engine.

Plan T5: 100% coverage on backtest/engine.py + determinism + smoke tests.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Iterable

import pytest

from backtest.engine import (
    BacktestParams,
    WindowResult,
    _classify,
    _mid,
    _simulate_window,
    _taker_fee,
    group_by_cid,
    iter_ticks,
    load_ticks,
    replay,
)


UP_TOKEN = "0xAAAA_up_token"
DN_TOKEN = "0xBBBB_dn_token"
CID = "0xCID_001"
SLUG = "btc-updown-5m-1788000000"
SERIES = "btc-up-or-down-5m"
DUR = 300


def snap(ts: float, mid_up: float, down_ask: float = 0.49, up_ask: float = 0.49,
         tape: list[dict] | None = None, queue_bids: dict | None = None,
         down_bids: dict | None = None, up_bids: dict | None = None,
         iso: str = "2026-08-29T00:00:00+00:00") -> dict:
    """Build a minimal tick dict with the shape collect_ticks writes.

    `mid_up` is authoritative — the book's bb/ba are derived so that
    (bb+ba)/2 == mid_up. Default up_ask/down_ask=0.49 keeps touch_pair <= 0.99
    so the default pair_cost_gate=0.995 lets the test through.
    """
    half = 0.005
    bb_up = round(mid_up - half, 4)
    ba_up = round(mid_up + half, 4)
    # If caller pinned up_ask explicitly, snap the book to it but keep mid pinned.
    if up_ask is not None and up_ask != ba_up:
        ba_up = up_ask
        bb_up = round(mid_up - (ba_up - mid_up), 4)
    return {
        "ts": ts, "iso": iso, "series": SERIES, "duration": DUR,
        "label": "BTC 5m", "cid": CID, "slug": SLUG,
        "start_ts": ts - 2.0, "end_ts": ts + 298.0, "t_rem": 298.0,
        "up_token": UP_TOKEN, "down_token": DN_TOKEN,
        "up_book": {
            "token_id": UP_TOKEN, "bids": up_bids or {}, "asks": {},
            "best_bid": bb_up, "best_ask": ba_up, "malformed": 0,
        },
        "down_book": {
            "token_id": DN_TOKEN, "bids": down_bids or {}, "asks": {},
            "best_bid": round(down_ask - 0.005, 4), "best_ask": down_ask, "malformed": 0,
        },
        "tape_delta": tape or [],
        "mid": mid_up, "touch_pair": up_ask + down_ask,
        "resting_pair": 0.96, "queue_up": 0.0, "queue_down": 0.0,
        "err": None,
    }


# --- helpers ---------------------------------------------------------------

def test_mid_both_sides():
    assert _mid({"best_bid": 0.30, "best_ask": 0.32}) == 0.31

def test_mid_bid_only():
    assert _mid({"best_bid": 0.30, "best_ask": None}) == 0.305

def test_mid_ask_only():
    assert _mid({"best_bid": None, "best_ask": 0.32}) == 0.315

def test_mid_empty():
    assert _mid({}) is None
    assert _mid({"best_bid": None, "best_ask": None}) is None

def test_taker_fee_zero_at_ends():
    assert _taker_fee(0.0, 0.07) == 0.0
    assert _taker_fee(1.0, 0.07) == 0.0
    assert _taker_fee(None, 0.07) == 0.0

def test_taker_fee_peak_at_half():
    assert abs(_taker_fee(0.50, 0.07) - 0.0175) < 1e-9

def test_classify_oscillating():
    assert _classify([0.48, 0.52, 0.49, 0.53]) == "oscillating"

def test_classify_monotonic():
    assert _classify([0.50, 0.55, 0.60, 0.70]) == "monotonic"

def test_classify_flat():
    assert _classify([0.495, 0.502, 0.498, 0.505]) == "flat"

def test_classify_no_data():
    assert _classify([]) == "no_data"

def test_params_hash_deterministic():
    p1 = BacktestParams(offset=0.02, queue_gate=50.0)
    p2 = BacktestParams(offset=0.02, queue_gate=50.0)
    p3 = BacktestParams(offset=0.03, queue_gate=50.0)
    assert p1.params_hash() == p2.params_hash()
    assert p1.params_hash() != p3.params_hash()

def test_exit_thresh_per_slug():
    p = BacktestParams()
    assert p.exit_thresh("btc-up-or-down-5m", 300) == 0.05
    assert p.exit_thresh("sol-up-or-down-5m", 300) == 0.05
    assert p.exit_thresh("eth-up-or-down-5m", 300) == 0.05   # default_5m
    assert p.exit_thresh("xrp-up-or-down-15m", 900) == 0.05  # default_15m

def test_exit_thresh_override():
    p = BacktestParams(exit_thresh_by_slug={"default_5m": 0.15})
    assert p.exit_thresh("eth-up-or-down-5m", 300) == 0.15


# --- loaders / group_by_cid -----------------------------------------------

def test_load_ticks_from_list_of_dicts(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    f.write_text('{"a":1}\n{"a":2}\n\nnot-valid\n{"a":4}\n', encoding="utf-8")
    out = list(iter_ticks(f))
    assert [x["a"] for x in out] == [1, 2, 4]

def test_load_ticks_from_dir_mixed(tmp_path: Path):
    f1 = tmp_path / "a.jsonl"
    f1.write_text('{"cid":"x","ts":2}\n', encoding="utf-8")
    f2 = tmp_path / "b.jsonl.gz"
    import gzip
    with gzip.open(f2, "wt", encoding="utf-8") as gz:
        gz.write('{"cid":"y","ts":1}\n')
    out = list(iter_ticks(tmp_path))
    assert sorted(x["ts"] for x in out) == [1, 2]

def test_load_ticks_rejects_bad_source():
    with pytest.raises(TypeError):
        list(iter_ticks(123))

def test_group_by_cid_orders_within_and_between():
    snaps = [
        snap(2.0, 0.50),  # cid default
        snap(1.0, 0.51, up_bids={"0.50": 1.0}),
        snap(3.0, 0.49, up_bids={"0.49": 1.0}),
    ]
    out = group_by_cid(snaps)
    assert len(out) == 1
    cid, group = out[0]
    assert cid == CID
    assert [s["ts"] for s in group] == [1.0, 2.0, 3.0]

def test_group_by_cid_multiple_cids():
    snaps = [
        snap(2.0, 0.50),
        {**snap(1.0, 0.51), "cid": "0xOTHER", "series": "eth-up-or-down-5m"},
    ]
    out = group_by_cid(snaps)
    assert [c for c, _ in out] == ["0xOTHER", CID]   # sorted by first ts

def test_group_by_cid_skips_blank_cid():
    snaps = [{**snap(1.0, 0.5), "cid": ""}, snap(2.0, 0.5)]
    out = group_by_cid(snaps)
    assert len(out) == 1


# --- simulation: fills -----------------------------------------------------

def test_simulate_pair_capture_via_tape():
    tape = [
        {"asset": UP_TOKEN, "price": 0.48, "size": 10.0},  # UP hit
    ]
    snaps = [snap(1.0, 0.50, up_ask=0.49, down_ask=0.49, tape=tape)]
    w = _simulate_window(snaps, BacktestParams())
    assert w.filled_up is True
    assert w.filled_down is False
    assert w.pair_captured is False

def test_simulate_pair_capture_both_sides():
    tape = [
        {"asset": UP_TOKEN, "price": 0.48, "size": 10.0},
        {"asset": DN_TOKEN, "price": 0.48, "size": 10.0},
    ]
    snaps = [snap(1.0, 0.50, up_ask=0.49, down_ask=0.49, tape=tape)]
    w = _simulate_window(snaps, BacktestParams())
    assert w.filled_up is True
    assert w.filled_down is True
    assert w.pair_captured is True
    assert w.pnl_cents > 0  # +4 gross minus tiny gas share

def test_simulate_tape_within_tick_tolerance():
    tape = [{"asset": UP_TOKEN, "price": 0.48 + 0.0005, "size": 5.0}]
    snaps = [snap(1.0, 0.50, up_ask=0.49, down_ask=0.49, tape=tape)]
    w = _simulate_window(snaps, BacktestParams(tick_size=0.001))
    assert w.filled_up is True

def test_simulate_tape_outside_tick_tolerance_no_fill():
    tape = [{"asset": UP_TOKEN, "price": 0.48 + 0.01, "size": 5.0}]
    snaps = [snap(1.0, 0.50, up_ask=0.49, down_ask=0.49, tape=tape)]
    w = _simulate_window(snaps, BacktestParams(tick_size=0.001))
    assert w.filled_up is False

def test_simulate_book_only_fill():
    # mid = 0.4845, offset 0.005, resting_up = 0.48
    # up_ask 0.479 <= 0.48 -> book crossed -> fill UP
    # resting_down = (1-0.4845) - 0.005 = 0.5105, down_ask 0.49 > 0.5105 -> no fill DOWN
    snaps = [{
        "ts": 1.0, "iso": "x", "series": SERIES, "duration": DUR, "label": "BTC 5m",
        "cid": CID, "slug": SLUG, "start_ts": 0.0, "end_ts": 300.0, "t_rem": 300.0,
        "up_token": UP_TOKEN, "down_token": DN_TOKEN,
        "up_book": {"token_id": UP_TOKEN, "bids": {}, "asks": {},
                    "best_bid": 0.49, "best_ask": 0.479, "malformed": 0},
        "down_book": {"token_id": DN_TOKEN, "bids": {}, "asks": {},
                      "best_bid": 0.52, "best_ask": 0.52, "malformed": 0},
        "tape_delta": [], "mid": 0.4845, "touch_pair": 0.999,
        "resting_pair": 0.96, "queue_up": 0.0, "queue_down": 0.0, "err": None,
    }]
    w = _simulate_window(snaps, BacktestParams(offset=0.005, fill_model="book",
                                                pair_cost_gate=1.00))
    assert w.filled_up is True
    assert w.filled_down is False

def test_simulate_book_only_no_fill_when_ask_above_resting():
    snaps = [snap(1.0, 0.50, up_ask=0.49, down_ask=0.49)]
    w = _simulate_window(snaps, BacktestParams(fill_model="book"))
    assert w.filled_up is False

def test_simulate_both_models_flag_both_fills():
    tape = [{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]
    snaps = [snap(1.0, 0.50, up_ask=0.479, down_ask=0.479, tape=tape)]
    w = _simulate_window(snaps, BacktestParams(fill_model="both"))
    assert w.filled_up is True
    assert w.filled_down is True

def test_simulate_cross_model_requires_strict_crossing():
    # At offset=0.02 (resting at 0.48), a trade at 0.48 DOES NOT fill in cross model.
    tape_48 = [{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]
    snaps_48 = [snap(1.0, 0.50, up_ask=0.51, down_ask=0.51, tape=tape_48)]
    w_48 = _simulate_window(snaps_48, BacktestParams(offset=0.02, fill_model="cross"))
    assert w_48.filled_up is False

    # A trade that crosses through at 0.47 DOES fill in cross model.
    tape_47 = [{"asset": UP_TOKEN, "price": 0.47, "size": 5.0}]
    snaps_47 = [snap(1.0, 0.50, up_ask=0.51, down_ask=0.51, tape=tape_47)]
    w_47 = _simulate_window(snaps_47, BacktestParams(offset=0.02, fill_model="cross"))
    assert w_47.filled_up is True

    # Book ask at 0.48 DOES NOT fill in cross model (must be <= 0.47).
    snaps_ask_48 = [snap(1.0, 0.50, up_ask=0.48, down_ask=0.51)]
    w_ask_48 = _simulate_window(snaps_ask_48, BacktestParams(offset=0.02, fill_model="cross"))
    assert w_ask_48.filled_up is False

    # Book ask at 0.47 DOES fill in cross model.
    snaps_ask_47 = [snap(1.0, 0.50, up_ask=0.47, down_ask=0.51)]
    w_ask_47 = _simulate_window(snaps_ask_47, BacktestParams(offset=0.02, fill_model="cross"))
    assert w_ask_47.filled_up is True

def test_simulate_cross_model_pair_capture():
    # Both legs cross through 0.48 to 0.47 -> Pair captured (+4c profit)
    tape = [
        {"asset": UP_TOKEN, "price": 0.47, "size": 5.0},
        {"asset": DN_TOKEN, "price": 0.47, "size": 5.0},
    ]
    snaps = [snap(1.0, 0.50, up_ask=0.51, down_ask=0.51, tape=tape)]
    w = _simulate_window(snaps, BacktestParams(offset=0.02, fill_model="cross"))
    assert w.filled_up is True
    assert w.filled_down is True
    assert w.pair_captured is True
    assert w.pnl_cents == 4.0

def test_simulate_cross_model_exit_on_drift():
    # UP leg crosses to 0.47 on tape and fills; DOWN ask stays at 0.53 (above 0.48 resting, no fill).
    # Mid drifts to 0.40 (max_down = 0.10 >= 0.08 exit threshold) -> Safety exit triggered on UP.
    snap1 = snap(1.0, 0.50, up_ask=0.49, down_ask=0.53,
                 tape=[{"asset": UP_TOKEN, "price": 0.47, "size": 5.0}])
    snap2 = snap(2.0, 0.40, up_ask=0.41, down_ask=0.61,
                 up_bids={"0.39": 100.0})
    w = _simulate_window([snap1, snap2], BacktestParams(
        offset=0.02,
        fill_model="cross",
        exit_thresh_by_slug={"btc-up-or-down-5m": 0.08, "default_5m": 0.08},
    ))
    assert w.filled_up is True
    assert w.filled_down is False
    assert w.exit_taken is True
    assert w.exit_side == "up"





# --- simulation: gates ----------------------------------------------------

def test_simulate_queue_gate_blocks_entry():
    snaps = [snap(1.0, 0.50, up_bids={"0.48": 1000.0})]
    w = _simulate_window(snaps, BacktestParams(queue_gate=50.0))
    assert w.filled_up is False
    assert w.filled_down is False

def test_simulate_queue_gate_zero_disables():
    # With queue_gate=0, gate is bypassed — tape-confirmed fill at 0.48 succeeds.
    tape = [{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]
    snaps = [snap(1.0, 0.50, up_bids={"0.48": 10000.0}, tape=tape)]
    w = _simulate_window(snaps, BacktestParams(queue_gate=0.0))
    assert w.filled_up is True

def test_simulate_pair_cost_gate_blocks_wide_touch():
    tape = [{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]
    snaps = [snap(1.0, 0.50, up_ask=0.60, down_ask=0.60, tape=tape)]
    w = _simulate_window(snaps, BacktestParams(pair_cost_gate=0.99))
    assert w.filled_up is False

def test_simulate_pair_cost_gate_zero_disables():
    # With pair_cost_gate=0, gate is bypassed even if touch is very wide (1.20)
    tape = [{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]
    snaps = [snap(1.0, 0.50, up_ask=0.60, down_ask=0.60, tape=tape)]
    w = _simulate_window(snaps, BacktestParams(pair_cost_gate=0.0))
    assert w.filled_up is True


# --- simulation: exit -----------------------------------------------------

def test_simulate_exit_when_one_side_filled_and_drifts():
    # mid starts at 0.50, UP fills at 0.48, then mid drifts down to 0.38 (max_down = 0.12 > 0.09)
    snaps = [
        snap(1.0, 0.50, up_ask=0.49, down_ask=0.51,
             tape=[{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]),
        snap(2.0, 0.45, up_ask=0.45, down_ask=0.55),
        snap(3.0, 0.40, up_ask=0.40, down_ask=0.60),
        snap(4.0, 0.38, up_ask=0.38, down_ask=0.62),
    ]
    w = _simulate_window(snaps, BacktestParams())
    assert w.filled_up is True
    assert w.exit_taken is True
    assert w.exit_side == "up"
    assert w.pnl_cents < 0   # loss on naked UP

def test_simulate_exit_when_down_filled_and_up_drifts():
    # DOWN fills at 0.48, mid drifts up to 0.62 (max_up = 0.12 > 0.09 threshold)
    snaps = [
        snap(1.0, 0.50, up_ask=0.51, down_ask=0.49,
             tape=[{"asset": DN_TOKEN, "price": 0.48, "size": 5.0}]),
        snap(2.0, 0.55, up_ask=0.55, down_ask=0.45),
        snap(3.0, 0.60, up_ask=0.60, down_ask=0.40),
        snap(4.0, 0.62, up_ask=0.62, down_ask=0.38),
    ]
    w = _simulate_window(snaps, BacktestParams())
    assert w.filled_down is True
    assert w.exit_taken is True
    assert w.exit_side == "down"
    assert w.pnl_cents < 0   # loss on naked DOWN

def test_simulate_no_exit_when_reversal_seen():
    # Sequence: downward excursion first (setting reversal_seen_down),
    # then return to 0.50 where UP fills, then drift down past threshold.
    # Because reversal was seen, it does not trigger an exit.
    snaps = [
        snap(1.0, 0.50, up_ask=0.49, down_ask=0.51),
        snap(2.0, 0.40, up_ask=0.40, down_ask=0.60),    # +0.10 down excursion -> sets reversal_seen_down
        snap(3.0, 0.50, up_ask=0.49, down_ask=0.51,
             tape=[{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]), # UP fills at 0.50
        snap(4.0, 0.40, up_ask=0.40, down_ask=0.60),    # +0.10 down again past 0.09 threshold
    ]
    w = _simulate_window(snaps, BacktestParams(exit_thresh_by_slug={"btc-up-or-down-5m": 0.09}))
    assert w.exit_taken is False

def test_simulate_exit_below_unified_threshold():
    """Verify exit is not taken when drift is below the unified 0.05 threshold."""
    snaps = [
        snap(1.0, 0.50, up_ask=0.49, down_ask=0.51,
             tape=[{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}]),
        snap(2.0, 0.46, up_ask=0.46, down_ask=0.54),   # 0.50 - 0.46 = +0.04 max_down, below 0.05
    ]
    w = _simulate_window(snaps, BacktestParams())
    assert w.exit_taken is False


# --- simulation: classification + edges ----------------------------------

def test_simulate_classifies_oscillating():
    snaps = [snap(1.0, 0.48, up_ask=0.48, down_ask=0.52),
             snap(2.0, 0.52, up_ask=0.52, down_ask=0.48)]
    w = _simulate_window(snaps, BacktestParams())
    assert w.class_label == "oscillating"
    assert w.max_up >= 0.02
    assert w.max_down >= 0.02

def test_simulate_empty_window():
    w = _simulate_window([], BacktestParams())
    assert w.class_label == "no_data"
    assert w.n_snaps == 0
    assert w.err == "empty"

def test_simulate_skips_none_mid_without_crashing():
    snap_no_mid = snap(1.0, 0.50)
    snap_no_mid["up_book"]["best_bid"] = None
    snap_no_mid["up_book"]["best_ask"] = None
    snap_no_mid["mid"] = None
    w = _simulate_window([snap_no_mid, snap(2.0, 0.51)], BacktestParams())
    assert w.n_snaps == 2
    assert w.class_label in ("monotonic", "flat", "no_data", "oscillating")


# --- replay: determinism + aggregates ------------------------------------

def _two_window_dataset() -> list[dict]:
    """One oscillating 5m window + one monotonic 5m window."""
    base = 1_000_000.0
    osc = [
        snap(base + i, 0.50 + 0.01 * (i if i < 3 else 3 - i))
        for i in range(6)
    ]
    osc[0] = snap(base, 0.50, tape=[{"asset": UP_TOKEN, "price": 0.48, "size": 5.0}])
    osc[3] = snap(base + 3, 0.50, tape=[{"asset": DN_TOKEN, "price": 0.48, "size": 5.0}])

    mono_cid = "0xMONO_001"
    mono = []
    for i in range(5):
        d = {**snap(base + 10 + i, 0.50 + 0.05 * i, up_ask=0.55 + 0.05*i,
                    down_ask=0.45 - 0.05*i), "cid": mono_cid, "slug": "x-5m-mono",
             "series": "eth-up-or-down-5m", "duration": 300}
        mono.append(d)
    return osc + mono


def test_replay_returns_aggregate_and_per_window():
    out = replay(_two_window_dataset(), BacktestParams())
    assert out["n_windows"] == 2
    assert "overall" in out["aggregate"]
    assert "per_series" in out["aggregate"]
    assert out["params_hash"] == BacktestParams().params_hash()
    assert "equity_curve" in out
    assert len(out["equity_curve"]) == 2
    assert "trades_sample" in out
    assert len(out["trades_sample"]) == 2
    assert "max_drawdown_cents" in out["aggregate"]["overall"]
    assert "win_rate" in out["aggregate"]["overall"]


def test_replay_aggregates_reentry_by_series():
    """Aggregate reports how many windows each series recovered via re-entry."""
    # Adverse open (mid 0.35, drift 0.15) at t=102s; the mid reverts to 0.50 at
    # t=110s with both legs filling at the 0.48 resting price -> re-entry fires.
    reentry_cid = "0xRE_001"
    reentry_snaps = [
        {**snap(102.0, 0.35, up_ask=0.355, down_ask=0.655), "cid": reentry_cid},
        {**snap(110.0, 0.50, up_ask=0.505, down_ask=0.505,
                tape=[{"asset": UP_TOKEN, "price": 0.48, "size": 5.0},
                      {"asset": DN_TOKEN, "price": 0.48, "size": 5.0}]),
         "cid": reentry_cid},
    ]
    # A plain healthy pair in another series is never re-entered.
    healthy_snaps = [
        {**snap(202.0, 0.50, up_ask=0.505, down_ask=0.505,
                tape=[{"asset": UP_TOKEN, "price": 0.48, "size": 5.0},
                      {"asset": DN_TOKEN, "price": 0.48, "size": 5.0}]),
         "cid": "0xRE_002", "series": "eth-up-or-down-5m"},
    ]
    # min_requote_remaining_sec defaults to a whole 5m window (issue #89's shared
    # knob), so lower it to let these 5m replays re-enter at all.
    out = replay(reentry_snaps + healthy_snaps,
                 BacktestParams(min_requote_remaining_sec=60.0))

    btc = out["aggregate"]["per_series"][SERIES]
    assert btc["windows"] == 1
    assert btc["reentry_count"] == 1
    assert btc["reentry_pnl_cents"] == pytest.approx(4.0, abs=1e-3)

    eth = out["aggregate"]["per_series"]["eth-up-or-down-5m"]
    assert eth["windows"] == 1
    assert eth["reentry_count"] == 0
    assert eth["reentry_pnl_cents"] == 0.0

    ov = out["aggregate"]["overall"]
    assert ov["reentry_count"] == 1
    assert ov["reentry_pnl_cents"] == pytest.approx(4.0, abs=1e-3)

    recovered = next(w for w in out["per_window"] if w["cid"] == reentry_cid)
    assert recovered["reentry_count"] == 1


def test_replay_is_deterministic():
    snaps = _two_window_dataset()
    a = replay(snaps, BacktestParams())
    b = replay(snaps, BacktestParams())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_replay_different_params_different_hash():
    snaps = _two_window_dataset()
    a = replay(snaps, BacktestParams(offset=0.01))
    b = replay(snaps, BacktestParams(offset=0.03))
    assert a["params_hash"] != b["params_hash"]


def test_replay_handles_empty_input():
    out = replay([], BacktestParams())
    assert out["n_windows"] == 0
    assert out["aggregate"]["overall"]["windows"] == 0
    assert out["equity_curve"] == []
    assert out["trades_sample"] == []
    assert out["aggregate"]["overall"]["max_drawdown_cents"] == 0.0
    assert out["aggregate"]["overall"]["win_rate"] == 0.0


def test_replay_equity_curve_and_kpi_calculations():
    snaps = _two_window_dataset()
    out = replay(snaps, BacktestParams())
    eq = out["equity_curve"]
    assert len(eq) == 2
    assert eq[0]["window"] == 1
    assert eq[1]["window"] == 2
    assert isinstance(eq[0]["pnl"], float)
    ov = out["aggregate"]["overall"]
    assert 0.0 <= ov["win_rate"] <= 1.0
    assert ov["max_drawdown_cents"] >= 0.0


def test_replay_skips_blanks_and_bad_lines(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    f.write_text('{"cid":"z","ts":1,"series":"a","duration":300,'
                 '"slug":"a","up_book":{},"down_book":{},"tape_delta":[]}\n'
                 "\n"
                 "not-json\n", encoding="utf-8")
    out = replay(list(iter_ticks(f)), BacktestParams())
    assert out["n_windows"] == 1


def test_simulate_window_start_delay_and_partial_flag():
    # Snap with 10s start delay (>5s -> partial)
    snap_late = snap(110.0, 0.50)
    snap_late["start_ts"] = 100.0
    w_late = _simulate_window([snap_late], BacktestParams())
    assert w_late.start_delay_sec == 10.0
    assert w_late.is_partial is True

    # Snap with 2s start delay (<=5s -> full window)
    snap_early = snap(102.0, 0.50)
    snap_early["start_ts"] = 100.0
    w_early = _simulate_window([snap_early], BacktestParams())
    assert w_early.start_delay_sec == 2.0
    assert w_early.is_partial is False


def test_replay_max_start_delay_filtering():
    # Window 1: delay = 10s (late)
    w1_snap = {**snap(110.0, 0.50), "cid": "0xW1", "start_ts": 100.0}
    # Window 2: delay = 1s (early)
    w2_snap = {**snap(201.0, 0.50), "cid": "0xW2", "start_ts": 200.0}
    snaps = [w1_snap, w2_snap]

    # No filter (default max_start_delay_sec = 0.0) -> both windows included
    out_all = replay(snaps, BacktestParams())
    assert out_all["n_windows"] == 2

    # Filter max_start_delay_sec = 5.0 -> only w2 included
    out_filtered = replay(snaps, BacktestParams(max_start_delay_sec=5.0))
    assert out_filtered["n_windows"] == 1
    assert out_filtered["trades_sample"][0]["slug"] == w2_snap["slug"]
    assert out_filtered["trades_sample"][0]["is_partial"] is False


def test_backtest_dynamic_symmetric_quoting_off_center_open():
    """Verify that backtest replay dynamically anchors resting quotes to initial leg mids

    instead of hardcoding to 0.48 / 0.50 - offset.
    For an off-center open where UP mid is 0.35 and DOWN mid is 0.65:
    resting_up should be 0.35 - 0.02 = 0.33
    resting_down should be 0.65 - 0.02 = 0.63
    """
    # snap with UP mid=0.35 (ba=0.355, bb=0.345) and DOWN mid=0.65 (ba=0.655, bb=0.645)
    s1 = snap(1.0, 0.35, up_ask=0.355, down_ask=0.655)
    # Ensure down_book mid is 0.65:
    s1["down_book"]["best_bid"] = 0.645
    s1["down_book"]["best_ask"] = 0.655

    params = BacktestParams(offset=0.02, fill_model="cross", exit_thresh_by_slug={"default_5m": 0.20})

    # Case A: Trade on DOWN leg at 0.62. Under dynamic quoting (resting_down=0.63),
    # 0.62 is <= resting_down - 1c, so it fills in cross model.
    # Under old static 0.48, 0.62 would NEVER fill (0.62 > 0.48).
    s_down_fill = {**s1, "tape_delta": [{"asset": DN_TOKEN, "price": 0.62, "size": 5.0}]}
    w_down = _simulate_window([s_down_fill], params)
    assert w_down.filled_down is True, "DOWN leg should fill at 0.62 when resting_down is 0.63"

    # Case B: Trade on UP leg at 0.47. Under static 0.48 quoting, 0.47 would fill (0.47 <= 0.48 - 1c).
    # But under dynamic quoting (resting_up=0.33), 0.47 is far above 0.33 and should NOT fill.
    s_up_nofill = {**s1, "tape_delta": [{"asset": UP_TOKEN, "price": 0.47, "size": 5.0}]}
    w_up_nofill = _simulate_window([s_up_nofill], params)
    assert w_up_nofill.filled_up is False, "UP leg should NOT fill at 0.47 when resting_up is 0.33"

    # Case C: Both legs cross through dynamic quotes (UP at 0.32, DOWN at 0.62).
    # Pair should be captured with pnl_cents = (1.00 - (0.33 + 0.63)) * 100 = 4.0c
    s_pair = {**s1, "tape_delta": [
        {"asset": UP_TOKEN, "price": 0.32, "size": 5.0},
        {"asset": DN_TOKEN, "price": 0.62, "size": 5.0},
    ]}
    w_pair = _simulate_window([s_pair], params)
    assert w_pair.filled_up is True
    assert w_pair.filled_down is True
    assert w_pair.pair_captured is True
    assert round(w_pair.pnl_cents, 2) == 4.0


def test_window_result_execution_prices_pair_captured():
    """Verify WindowResult tracks entry prices on both legs when pair is captured."""
    s1 = snap(1.0, 0.50, up_ask=0.51, down_ask=0.51, tape=[
        {"asset": UP_TOKEN, "price": 0.47, "size": 5.0},
        {"asset": DN_TOKEN, "price": 0.47, "size": 5.0},
    ])
    params = BacktestParams(offset=0.02, fill_model="cross")
    w = _simulate_window([s1], params)
    assert w.pair_captured is True
    assert w.entry_price_up == 0.48
    assert w.entry_price_down == 0.48
    assert w.exit_price is None


def test_window_result_execution_prices_stop_exit():
    """Verify WindowResult tracks entry and exit prices on stop-loss exit."""
    snap1 = snap(1.0, 0.50, up_ask=0.49, down_ask=0.53,
                 tape=[{"asset": UP_TOKEN, "price": 0.47, "size": 5.0}])
    snap2 = snap(2.0, 0.40, up_ask=0.41, down_ask=0.61,
                 up_bids={"0.39": 100.0})
    w = _simulate_window([snap1, snap2], BacktestParams(
        offset=0.02,
        fill_model="cross",
        exit_thresh_by_slug={"btc-up-or-down-5m": 0.08, "default_5m": 0.08},
    ))
    assert w.filled_up is True
    assert w.exit_taken is True
    assert w.exit_side == "up"
    assert w.entry_price_up == 0.48
    assert w.entry_price_down is None
    assert w.exit_price == 0.39  # snap2 up_ask=0.41, mid=0.40 -> bb_up = 0.40 - (0.41 - 0.40) = 0.39


def test_replay_trades_sample_untruncated_with_prices():
    """Verify replay returns all windows in trades_sample (not capped at 50) and includes prices."""
    snaps = []
    for i in range(55):
        cid = f"0xCID_{i:03d}"
        s = {**snap(100.0 + i * 500, 0.50), "cid": cid}
        snaps.append(s)
    out = replay(snaps, BacktestParams())
    assert out["n_windows"] == 55
    assert len(out["trades_sample"]) == 55
    sample0 = out["trades_sample"][0]
    assert "entry_up" in sample0
    assert "entry_down" in sample0
    assert "exit_price" in sample0
    assert "exit_side" in sample0


# --- entry delay / entry band params (issue #145) ---------------------------

def test_entry_delay_band_defaults_off():
    p = BacktestParams()
    assert p.entry_delay_sec == 0.0
    assert p.entry_band == 0.0


def test_entry_delay_band_accept_winning_config():
    p = BacktestParams(entry_delay_sec=60.0, entry_band=0.04)
    assert p.entry_delay_sec == 60.0
    assert p.entry_band == 0.04


def test_entry_delay_band_reject_out_of_range():
    with pytest.raises(ValueError):
        BacktestParams(entry_delay_sec=-1.0)
    with pytest.raises(ValueError):
        BacktestParams(entry_delay_sec=3600.01)
    with pytest.raises(ValueError):
        BacktestParams(entry_band=-0.01)
    with pytest.raises(ValueError):
        BacktestParams(entry_band=0.51)


def test_entry_delay_band_grouped_as_trading_knobs():
    gp = BacktestParams().grouped_params()
    assert "entry_delay_sec" in gp["trading_knobs"]
    assert "entry_band" in gp["trading_knobs"]


def _window_snaps(n: int, mid_fn, tape_fn, start: float = 1000.0,
                  up_ask_fn=None, down_ask_fn=None) -> list[dict]:
    """Build an n-snap window with a FIXED start_ts so elapsed_i == i.

    snap() stamps start_ts = ts - 2 per tick; a real window shares one
    start_ts, so patch every snap to `start` (raw_start_delay == 0).
    """
    out = []
    for i in range(n):
        mid = mid_fn(i)
        kw = {}
        if up_ask_fn is not None:
            kw["up_ask"] = up_ask_fn(i)
        if down_ask_fn is not None:
            kw["down_ask"] = down_ask_fn(i)
        s = snap(start + i, mid, tape=tape_fn(i), **kw)
        s["start_ts"] = start
        out.append(s)
    return out


def _tape_both(up_price: float, down_price: float) -> list[dict]:
    return [
        {"asset": UP_TOKEN, "price": up_price, "size": 5.0},
        {"asset": DN_TOKEN, "price": down_price, "size": 5.0},
    ]


def test_entry_delay_holds_quotes_until_expiry():
    # Tape prints at resting (0.48/0.48) on snaps 0..59 only. With delay=60
    # nothing may fill; without delay the pair captures immediately.
    snaps = _window_snaps(70, lambda i: 0.50,
                          lambda i: _tape_both(0.48, 0.48) if i < 60 else [])
    w = _simulate_window(snaps, BacktestParams(entry_delay_sec=60.0,
                                               entry_timeout_pct=0.0))
    assert w.filled_up is False
    assert w.filled_down is False
    assert w.pair_captured is False
    w0 = _simulate_window(snaps, BacktestParams(entry_timeout_pct=0.0))
    assert w0.pair_captured is True


def test_entry_band_skips_decided_window():
    # Two-sided mid 0.60 at expiry (drift 0.10 > 0.04) -> skip, no fills.
    # exit_thresh is raised to 0.15 so the adverse-open gate (drift 0.10)
    # passes and only the band decides the window.
    no_adverse = {"default_5m": 0.15}
    snaps = _window_snaps(
        10, lambda i: 0.60, lambda i: _tape_both(0.58, 0.38),
        up_ask_fn=lambda i: 0.605, down_ask_fn=lambda i: 0.4025)
    w = _simulate_window(snaps, BacktestParams(entry_band=0.04,
                                               entry_timeout_pct=0.0,
                                               exit_thresh_by_slug=no_adverse))
    assert w.filled_up is False
    assert w.filled_down is False
    w0 = _simulate_window(snaps, BacktestParams(entry_timeout_pct=0.0,
                                                exit_thresh_by_slug=no_adverse))
    assert w0.pair_captured is True


def test_entry_band_admits_undecided_window():
    # Two-sided mid 0.51 (drift 0.01 <= 0.04) -> admitted, pair captures.
    snaps = _window_snaps(
        10, lambda i: 0.51, lambda i: _tape_both(0.49, 0.47),
        up_ask_fn=lambda i: 0.515, down_ask_fn=lambda i: 0.4925)
    w = _simulate_window(snaps, BacktestParams(entry_band=0.04,
                                               entry_timeout_pct=0.0))
    assert w.pair_captured is True


def test_entry_delay_anchors_quotes_post_delay():
    # Mid 0.50 before t=60, 0.55 after. Delay=60 must anchor at 0.55
    # (resting_up 0.53), not at the 0.50 open (0.48).
    def mid_fn(i):
        return 0.50 if i < 60 else 0.55
    snaps = _window_snaps(
        70, mid_fn, lambda i: _tape_both(0.53, 0.43) if i >= 60 else [],
        up_ask_fn=lambda i: 0.49 if i < 60 else 0.555,
        down_ask_fn=lambda i: 0.49 if i < 60 else 0.4525)
    w = _simulate_window(snaps, BacktestParams(entry_delay_sec=60.0,
                                               entry_timeout_pct=0.0))
    assert w.pair_captured is True
    assert w.entry_price_up == 0.53
    assert w.entry_price_down == 0.43


def test_entry_band_boundary_admits_inside_band():
    # Drift 0.0400..36 (float repr of 0.54-0.50) admits under band 0.041
    # (strict `>` skips); exit 0.05 lets it through. Mirrors live's raw
    # comparison — exact-decimal boundaries are not promised.
    snaps = _window_snaps(
        10, lambda i: 0.54, lambda i: _tape_both(0.52, 0.44),
        up_ask_fn=lambda i: 0.545, down_ask_fn=lambda i: 0.4625)
    w = _simulate_window(snaps, BacktestParams(entry_band=0.041,
                                               entry_timeout_pct=0.0))
    assert w.pair_captured is True


def test_entry_delay_classifies_full_path():
    # Observe-only delay: no fills, but classification uses the whole path.
    snaps = _window_snaps(70, lambda i: 0.45 if i % 2 == 0 else 0.55,
                          lambda i: [])
    w = _simulate_window(snaps, BacktestParams(entry_delay_sec=60.0,
                                               entry_timeout_pct=0.0))
    assert w.filled_up is False
    assert w.filled_down is False
    assert w.n_snaps == 70
    assert w.class_label == "oscillating"


def test_adverse_claimed_window_bypasses_band_then_reenters():
    # Drift 0.10 trips both gates; the adverse gate owns the window, so a
    # later revert to 0.50 recovers via re-entry (a band skip is final).
    def mid_fn(i):
        return 0.60 if i < 10 else 0.50
    snaps = _window_snaps(
        30, mid_fn, lambda i: [],
        up_ask_fn=lambda i: 0.605 if i < 10 else 0.505,
        down_ask_fn=lambda i: 0.4025 if i < 10 else 0.5025)
    w = _simulate_window(snaps, BacktestParams(
        entry_band=0.04, entry_timeout_pct=0.0, min_requote_remaining_sec=0.0))
    assert w.reentry_count == 1
    assert w.filled_up is False


def test_anchor_uses_mid_only_prefix():
    # First snaps carry s["mid"] but no quotable book: the anchor still
    # latches from s["mid"] (old pre-loop scan parity), not a later tick.
    snaps = _window_snaps(
        10, lambda i: 0.50, lambda i: _tape_both(0.50, 0.46),
        up_ask_fn=lambda i: 0.505, down_ask_fn=lambda i: 0.5025)
    for s in snaps[:3]:
        s["mid"] = 0.52
        s["up_book"] = {**s["up_book"], "best_bid": None, "best_ask": None}
    w = _simulate_window(snaps, BacktestParams(entry_timeout_pct=0.0))
    assert w.pair_captured is True
    assert w.entry_price_up == 0.50  # anchored at 0.52, not the 0.50 books


def test_entry_delay_band_changes_hash():
    p0 = BacktestParams()
    p1 = BacktestParams(entry_delay_sec=60.0, entry_band=0.04)
    assert p0.params_hash() != p1.params_hash()
    assert p1.params_hash() == BacktestParams(
        entry_delay_sec=60.0, entry_band=0.04).params_hash()


def test_reentry_deferred_until_delay_expiry():
    # Adverse tick at t=0, revert at t=1, tape pre-delay only. delay=60 must
    # not fill before expiry (live holds all quotes while delay pending);
    # the grant is deferred to t=60 (reentry_count==1), not dropped.
    # delay=0 control grants and fills at t=1.
    def mid_fn(i):
        return 0.60 if i == 0 else 0.50
    snaps = _window_snaps(
        70, mid_fn, lambda i: _tape_both(0.48, 0.48) if 1 <= i < 60 else [],
        up_ask_fn=lambda i: 0.605 if i == 0 else 0.505,
        down_ask_fn=lambda i: 0.4025 if i == 0 else 0.5025)
    w = _simulate_window(snaps, BacktestParams(
        entry_delay_sec=60.0, entry_timeout_pct=0.0,
        min_requote_remaining_sec=0.0))
    assert w.filled_up is False
    assert w.filled_down is False
    assert w.reentry_count == 1
    w0 = _simulate_window(snaps, BacktestParams(
        entry_timeout_pct=0.0, min_requote_remaining_sec=0.0))
    assert w0.pair_captured is True


# ===========================================================================
# Issue #164: stop_loss_enabled — mirrors LiveTraderEngine
# ===========================================================================

def _drift_window(start_ts=1_760_000_000.0, duration=300):
    """One window where UP fills, then the mid drifts down past any stop."""
    snaps = []
    # tick 0: both sides quotable at 0.50, nothing filled yet
    mids = [0.50, 0.50, 0.42, 0.38, 0.35, 0.33]
    for i, m in enumerate(mids):
        up_bid, up_ask = round(m - 0.01, 3), round(m + 0.01, 3)
        dn_bid, dn_ask = round(1 - m - 0.01, 3), round(1 - m + 0.01, 3)
        # after the first tick the UP ask collapses onto our resting bid
        if i == 1:
            up_ask = 0.47
        snaps.append({
            "ts": start_ts + i, "cid": "0xstop", "series": "eth-up-or-down-5m",
            "slug": "eth-up-or-down-5m", "start_ts": start_ts,
            "end_ts": start_ts + duration, "duration": duration, "mid": m,
            "up_book": {"best_bid": up_bid, "best_ask": up_ask,
                        "bids": {str(up_bid): 500.0}, "asks": {str(up_ask): 500.0}},
            "down_book": {"best_bid": dn_bid, "best_ask": dn_ask,
                          "bids": {str(dn_bid): 500.0}, "asks": {str(dn_ask): 500.0}},
            "tape_delta": [],
        })
    return snaps


def _params(**kw):
    base = dict(offset=0.02, queue_gate=0.0, pair_cost_gate=1.05,
                fill_model="book", entry_timeout_pct=0.0,
                max_start_elapsed_pct=0.0, exit_reversal=0.0,
                exit_thresh_by_slug={"default_5m": 0.05, "default_15m": 0.05})
    base.update(kw)
    return BacktestParams(**base)


def test_stop_loss_enabled_defaults_to_the_previous_behaviour():
    """True is today: a naked leg past the threshold stops out."""
    assert BacktestParams().stop_loss_enabled is True
    w = _simulate_window(_drift_window(), _params())
    assert w.exit_taken is True, "the drift stop no longer fires by default"
    assert w.exit_side == "up"


def test_disabling_the_stop_holds_the_naked_leg_to_settlement():
    """`patient_band_maker` runs with stop_loss_enabled=False.

    Same ticks, same thresholds — the only difference is the knob. The leg
    must ride the drift instead of being sold into it.
    """
    w = _simulate_window(_drift_window(), _params(stop_loss_enabled=False))
    assert w.exit_taken is False, "the stop fired with stop_loss_enabled=False"
    assert w.filled_up is True, "the leg should still have filled"


def test_the_knob_changes_pnl_rather_than_only_a_flag():
    """A flag that flips without moving P&L would prove nothing."""
    on = _simulate_window(_drift_window(), _params())
    off = _simulate_window(_drift_window(), _params(stop_loss_enabled=False))
    assert on.pnl_cents != off.pnl_cents, (
        "disabling the stop left P&L unchanged — the gate is not on the path "
        "that books the exit")


def test_disabling_the_stop_does_not_suppress_pairing():
    """Only the stop is gated; a window that pairs must still pair."""
    snaps = _drift_window()
    # collapse the DOWN ask too, so both legs fill and the pair merges
    snaps[1]["down_book"]["best_ask"] = 0.47
    w = _simulate_window(snaps, _params(stop_loss_enabled=False))
    assert w.pair_captured is True, "pairing broke when the stop was disabled"


def test_stop_loss_enabled_is_part_of_the_params_hash():
    """Sweep caches key on the hash; two strategies must not collide."""
    assert _params().params_hash() != _params(stop_loss_enabled=False).params_hash()


# ===========================================================================
# Issue #164: naked_leg_timeout_pct + exit_thresh_naked
# ===========================================================================

def _flat_naked_window(n_ticks=29, start_ts=1_760_000_000.0, duration=300,
                       fill_tick=1):
    """UP fills on `fill_tick`, then the mid sits near 0.50 for the rest.

    The point is to isolate the *time* stop: the drift never approaches the
    price stop, so anything that exits here exited on the clock. Ticks are 10s
    apart and stay inside `duration`.
    """
    snaps = []
    for i in range(n_ticks):
        m = 0.50
        up_bid, up_ask = 0.49, (0.47 if i == fill_tick else 0.51)
        dn_bid, dn_ask = 0.49, 0.51
        snaps.append({
            "ts": start_ts + i * 10.0, "cid": "0xnaked",
            "series": "eth-up-or-down-5m", "slug": "eth-up-or-down-5m",
            "start_ts": start_ts, "end_ts": start_ts + duration,
            "duration": duration, "mid": m,
            "up_book": {"best_bid": up_bid, "best_ask": up_ask,
                        "bids": {str(up_bid): 500.0}, "asks": {str(up_ask): 500.0}},
            "down_book": {"best_bid": dn_bid, "best_ask": dn_ask,
                          "bids": {str(dn_bid): 500.0}, "asks": {str(dn_ask): 500.0}},
            "tape_delta": [],
        })
    return snaps


def test_naked_timeout_defaults_to_off():
    """0 is today: a flat naked leg rides to the end of the window."""
    assert BacktestParams().naked_leg_timeout_pct == 0.0
    w = _simulate_window(_flat_naked_window(), _params())
    assert w.filled_up is True and w.filled_down is False
    assert w.exit_taken is False


def test_naked_leg_is_exited_once_the_horizon_passes():
    """A time stop, not a price stop — the mid never moves in this window."""
    w = _simulate_window(_flat_naked_window(), _params(naked_leg_timeout_pct=0.50))
    assert w.exit_taken is True, "the naked leg was never timed out"
    assert w.exit_side == "up"
    # The invariant that matters: drift never reached the price stop, so the
    # exit above cannot have been a price stop.
    assert max(w.max_up, w.max_down) < 0.05, (
        f"drift reached {max(w.max_up, w.max_down)} — at or past the 0.05 "
        "price stop, so this no longer isolates the time stop")


def test_the_clock_starts_when_the_leg_goes_naked_not_at_window_open():
    """A late fill must still get its full horizon (mirrors live)."""
    # Fill at tick 20 = 200s into a 300s window; the window's last tick is 280s.
    snaps = _flat_naked_window(fill_tick=20)
    w = _simulate_window(snaps, _params(naked_leg_timeout_pct=0.50))
    assert w.filled_up is True
    # 50% of 300s = 150s of naked time. The fill lands at 200s, so the window
    # ends before the horizon: measured from window open it would have fired.
    assert w.exit_taken is False, (
        "the timeout fired early — the clock is measured from window open, "
        "not from the moment the leg went naked")


def test_a_completed_pair_is_never_timed_out():
    """The clock resets when the leg stops being naked."""
    snaps = _flat_naked_window()
    snaps[1]["down_book"]["best_ask"] = 0.47      # both legs fill on tick 1
    w = _simulate_window(snaps, _params(naked_leg_timeout_pct=0.10))
    assert w.pair_captured is True
    assert w.exit_taken is False, "a merged pair was killed by the naked timeout"


def test_naked_stop_may_tighten_the_paired_stop_but_never_loosen_it():
    """Mirrors live `_naked_exit_thresh`: 0 or >= paired falls back to paired."""
    p = _params(exit_thresh_by_slug={"default_5m": 0.05, "default_15m": 0.05})
    assert p.naked_exit_thresh("eth-up-or-down-5m", 300) == pytest.approx(0.05)
    tight = _params(exit_thresh_naked=0.02,
                    exit_thresh_by_slug={"default_5m": 0.05, "default_15m": 0.05})
    assert tight.naked_exit_thresh("eth-up-or-down-5m", 300) == pytest.approx(0.02)
    loose = _params(exit_thresh_naked=0.09,
                    exit_thresh_by_slug={"default_5m": 0.05, "default_15m": 0.05})
    assert loose.naked_exit_thresh("eth-up-or-down-5m", 300) == pytest.approx(0.05), (
        "a naked stop above the paired stop must not loosen risk")


def test_a_tighter_naked_stop_exits_a_drift_the_paired_stop_would_ride():
    """The knob has to reach the exit comparison, not just the helper."""
    wide = _simulate_window(_drift_window(), _params())
    tight = _simulate_window(_drift_window(), _params(exit_thresh_naked=0.02))
    assert wide.exit_taken and tight.exit_taken
    assert tight.exit_price > wide.exit_price, (
        "the tighter naked stop should have exited earlier, at a better bid")


def test_naked_timeout_is_not_suppressed_by_disabling_the_price_stop():
    """Time stop and price stop are separate triggers, as they are live."""
    w = _simulate_window(_flat_naked_window(),
                         _params(stop_loss_enabled=False, naked_leg_timeout_pct=0.50))
    assert w.exit_taken is True, (
        "stop_loss_enabled=False suppressed the naked *time* stop — live "
        "treats them as independent triggers")


@pytest.mark.parametrize("kw", [
    {"naked_leg_timeout_pct": 1.01}, {"naked_leg_timeout_pct": -0.01},
    {"exit_thresh_naked": 0.51}, {"exit_thresh_naked": -0.01},
])
def test_out_of_range_naked_knobs_are_refused(kw):
    with pytest.raises(ValueError):
        BacktestParams(**kw)


# ===========================================================================
# Issue #164: enable_leg_chase — mirrors live issue #123 and sim2
# ===========================================================================

def _chaseable_window(start_ts=1_760_000_000.0, duration=300,
                      dn_ask=0.49, dn_ask_after=None, last_dn_ask=None):
    """UP fills on tick 1; afterwards DOWN's ask is what the chase aims at.

    `dn_ask` is deliberately low on the fill tick so the pair-cost entry gate
    lets the window in at all — with a high ask throughout, nothing fills and
    every assertion about the chase becomes vacuous. `dn_ask_after` is the ask
    the chase then has to deal with, and `last_dn_ask` overrides the final tick.
    """
    after = dn_ask if dn_ask_after is None else dn_ask_after
    snaps = []
    n = 12
    for i in range(n):
        up_bid, up_ask = 0.49, (0.47 if i == 1 else 0.51)
        a = dn_ask if i <= 1 else after
        if last_dn_ask is not None and i == n - 1:
            a = last_dn_ask
        snaps.append({
            "ts": start_ts + i * 10.0, "cid": "0xchase",
            "series": "eth-up-or-down-5m", "slug": "eth-up-or-down-5m",
            "start_ts": start_ts, "end_ts": start_ts + duration,
            "duration": duration, "mid": 0.50,
            "up_book": {"best_bid": up_bid, "best_ask": up_ask,
                        "bids": {str(up_bid): 500.0}, "asks": {str(up_ask): 500.0}},
            "down_book": {"best_bid": 0.47, "best_ask": a,
                          "bids": {"0.47": 500.0}, "asks": {str(a): 500.0}},
            "tape_delta": [],
        })
    return snaps


def test_leg_chase_defaults_to_off():
    """False is today: the passive DOWN quote never moves, so it never fills."""
    assert BacktestParams().enable_leg_chase is False
    w = _simulate_window(_chaseable_window(), _params())
    assert w.filled_up is True
    assert w.filled_down is False, "DOWN filled without the chase being enabled"
    assert w.pair_captured is False


def test_leg_chase_converts_a_naked_leg_into_a_pair():
    """The whole point of the knob (live issue #123)."""
    w = _simulate_window(_chaseable_window(), _params(enable_leg_chase=True))
    assert w.filled_down is True, "the chase never reached the DOWN ask"
    assert w.pair_captured is True


def test_the_chase_never_breaches_the_pair_cost_cap():
    """A chase that pairs above the cap is worse than no chase at all.

    Asserts on `chased_resting`, not on a captured pair. Two earlier versions
    passed with the cap deleted from the engine: one guarded on
    `if w.pair_captured:` in a window that could not clear the entry gate, and
    one used an ask equal to the cap, so `min()` had nothing to bite on.
    """
    cap = 0.98
    # Low ask on the fill tick so the window enters; a far ask afterwards so
    # the cap is the only thing that can stop the chase. entry_up is 0.48, so
    # floor((0.98 - 0.48) * 100) / 100 = 0.50 is the ceiling.
    w = _simulate_window(_chaseable_window(dn_ask=0.49, dn_ask_after=0.60),
                         _params(enable_leg_chase=True, pair_cost_gate=cap))
    assert w.filled_up is True, "the window never entered — fixture is vacuous"
    assert w.chased_leg == "down", "the chase never ran"
    assert w.chased_resting is not None
    assert w.chased_resting <= 0.50 + 1e-9, (
        f"chased DOWN to {w.chased_resting}, past the 0.50 the cap allows")
    assert (w.entry_price_up + w.chased_resting) <= cap + 1e-9, (
        f"a fill there would pair at {w.entry_price_up + w.chased_resting}, "
        f"over the {cap} cap")


def test_the_chase_only_raises_the_quote_never_lowers_it():
    """Lowering would walk away from a fill already within reach."""
    # The ask after the fill sits above what the cap allows (ceiling is
    # floor((1.05 - 0.48) * 100) / 100 = 0.57), so the chased leg never fills
    # and the chase keeps running — an ask the chase could reach would pair
    # immediately and the loop would break before the drop ever happened.
    w = _simulate_window(
        _chaseable_window(dn_ask=0.49, dn_ask_after=0.70, last_dn_ask=0.20),
        _params(enable_leg_chase=True, pair_cost_gate=1.05))
    assert w.chased_leg == "down", "the chase never ran"
    assert w.chased_resting >= 0.57 - 1e-9, (
        f"the resting DOWN bid followed the ask down to {w.chased_resting}")


def test_the_chase_does_not_move_a_leg_that_already_filled():
    """Only the unfilled leg is re-anchored; the filled one keeps its entry."""
    snaps = _chaseable_window()
    w = _simulate_window(snaps, _params(enable_leg_chase=True))
    # UP filled passively at mid - offset = 0.50 - 0.02 = 0.48
    assert w.entry_price_up == pytest.approx(0.48), (
        f"the filled UP leg's entry moved to {w.entry_price_up}")


def test_the_chase_stops_once_the_pair_is_captured():
    """No further re-anchoring after both legs are filled."""
    w = _simulate_window(_chaseable_window(), _params(enable_leg_chase=True))
    assert w.pair_captured is True
    assert (w.entry_price_up + w.entry_price_down) <= 1.05 + 1e-9


def test_enable_leg_chase_is_part_of_the_params_hash():
    """Chased and unchased runs must not collide in a sweep cache."""
    assert _params().params_hash() != _params(enable_leg_chase=True).params_hash()


def _blocked_exit_then_reversion():
    """Drift past a tight naked stop while the UP book has no bid, then revert.

    The missing best_bid is what makes this reachable: the exit cannot fire on
    the crossing tick, so the drift is still on the books when the mid comes
    back. With the round-trip guard armed at the paired threshold instead of
    the naked one, the exit then fires into a fully recovered market.
    """
    start, dur = 1_760_000_000.0, 300
    mids = [0.50, 0.50, 0.46, 0.46, 0.499, 0.499]
    no_bid = {2, 3, 4}
    snaps = []
    for i, m in enumerate(mids):
        ub, ua = round(m - 0.01, 3), (0.47 if i == 1 else round(m + 0.01, 3))
        db, da = round(1 - m - 0.01, 3), round(1 - m + 0.01, 3)
        up = {"best_bid": (None if i in no_bid else ub), "best_ask": ua,
              "bids": ({} if i in no_bid else {str(ub): 500.0}),
              "asks": {str(ua): 500.0}}
        snaps.append({
            "ts": start + i * 10, "cid": "0xrev", "series": "eth-up-or-down-5m",
            "slug": "eth-up-or-down-5m", "start_ts": start, "end_ts": start + dur,
            "duration": dur, "mid": m, "up_book": up,
            "down_book": {"best_bid": db, "best_ask": da,
                          "bids": {str(db): 500.0}, "asks": {str(da): 500.0}},
            "tape_delta": [],
        })
    return snaps


def test_a_tight_naked_stop_arms_the_reversal_guard_at_its_own_threshold():
    """A round trip must suppress the stop that the round trip round-tripped.

    Regression: `exit_thresh_naked` tightened the exit comparison to
    `naked_thr` while the reversal latch still waited for the looser paired
    `exit_thr`. A drift past 0.03 that the book could not act on, followed by a
    full reversion to 0.499, then exited at 0.489 — selling into a recovered
    market on a stale drift.
    """
    w = _simulate_window(_blocked_exit_then_reversion(),
                         _params(exit_thresh_naked=0.03))
    assert w.filled_up is True, "fixture never entered"
    assert w.max_down >= 0.03, "fixture never crossed the tight stop"
    assert w.exit_taken is False, (
        f"stopped out at {w.exit_price} after the mid had reverted to 0.499 — "
        "the reversal guard is armed at the paired threshold, not the naked one")


def test_the_reversal_guard_is_unchanged_when_the_naked_stop_is_not_tightened():
    """`naked_thr` equals `exit_thr` by default, so this path must not move."""
    w = _simulate_window(_blocked_exit_then_reversion(), _params())
    assert w.exit_taken is False
    tight = _params(exit_thresh_naked=0.05)   # equal to paired: falls back
    assert tight.naked_exit_thresh("eth-up-or-down-5m", 300) == pytest.approx(0.05)


def test_the_chase_does_not_move_a_quote_with_no_ask_to_anchor_to():
    """Live wraps its whole chase in `if <leg>_ask is not None`.

    Without that gate the backtest advanced the resting price to the pair-cost
    ceiling on a tick where the book showed no ask at all — resting the leg
    where the live engine never would, and under `fill_model="tape"` (which
    needs no ask to fill) manufacturing a fill live could not have produced.
    """
    snaps = _chaseable_window(dn_ask=0.49, dn_ask_after=0.70)
    for s in snaps[2:]:                      # blind the DOWN book after the fill
        s["down_book"]["best_ask"] = None
        s["down_book"]["asks"] = {}
    w = _simulate_window(snaps, _params(enable_leg_chase=True, pair_cost_gate=1.05))
    assert w.filled_up is True, "fixture never entered"
    assert w.chased_leg == "", (
        f"the chase moved the DOWN leg to {w.chased_resting} with no ask on "
        "the book — live would not have quoted at all")


def test_the_chase_resumes_once_an_ask_reappears():
    """The gate must skip the blind tick, not disable the chase for the window."""
    snaps = _chaseable_window(dn_ask=0.49, dn_ask_after=0.70)
    for s in snaps[2:5]:
        s["down_book"]["best_ask"] = None
        s["down_book"]["asks"] = {}
    w = _simulate_window(snaps, _params(enable_leg_chase=True, pair_cost_gate=1.05))
    assert w.chased_leg == "down", "the chase never resumed after the ask returned"
    assert w.chased_resting is not None


def test_nothing_closes_a_naked_leg_when_both_stops_are_off():
    """`stop_loss_enabled=False` with no timeout is newly reachable.

    Neither stop can fire, so the leg must still be marked to the final book
    rather than silently contributing nothing.
    """
    w = _simulate_window(_drift_window(),
                         _params(stop_loss_enabled=False, naked_leg_timeout_pct=0.0))
    assert w.filled_up is True and w.filled_down is False
    assert w.exit_taken is False
    assert w.settlement_mid is not None, (
        "a naked leg with both stops disabled was never marked to settlement")
    assert w.pnl_cents != 0.0, "the held leg contributed no P&L at all"


def test_a_naked_stop_equal_to_the_paired_stop_falls_back_to_it():
    """The `>=` boundary, not just the strictly-looser case."""
    p = _params(exit_thresh_naked=0.05,
                exit_thresh_by_slug={"default_5m": 0.05, "default_15m": 0.05})
    assert p.naked_exit_thresh("eth-up-or-down-5m", 300) == pytest.approx(0.05)
