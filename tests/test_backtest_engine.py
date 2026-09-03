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
    snap1 = snap(1.0, 0.48, up_ask=0.49, down_ask=0.53,
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

