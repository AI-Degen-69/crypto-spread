"""Tests for the dead-zone & naked-leg measurement lab (issues #222 and #223).

The lab is measurement-only: every test here runs on synthetic windows and
pure functions — no dataset, no cache, no engine code touched. The fill rule
under test is `book_math.resting_bid_filled` itself (issue #226), reached
through the lab's timeline walker, so the tests also pin that the lab never
reimplements it.
"""
from __future__ import annotations

import math
import sys
from array import array
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "research" / "sweeps")):
    if p not in sys.path:
        sys.path.insert(0, p)

from strategy import book_math  # noqa: E402
from ev_lab import Win, SIDE_BUY, SIDE_SELL  # noqa: E402
import dead_zone_lab as lab  # noqa: E402


# ---------------------------------------------------------------------------
# synthetic window builder
# ---------------------------------------------------------------------------

def make_win(ts, up_bb, up_ba, dn_bb, dn_ba, tape=None, duration=300,
             start_ts=1000.0, cid="0xTEST", series="btc-up-or-down-5m",
             slug="btc-up-or-down-5m"):
    n = len(ts)
    d = {
        "cid": cid, "series": series, "slug": slug, "duration": duration,
        "start_ts": start_ts, "day": "2026-09-14",
        "ts": list(ts),
        "s_mid": [None] * n,
        "up_bb": list(up_bb), "up_ba": list(up_ba),
        "dn_bb": list(dn_bb), "dn_ba": list(dn_ba),
        "up_bids": [array("d") for _ in range(n)],
        "dn_bids": [array("d") for _ in range(n)],
        "tape": list(tape) if tape is not None else [([], []) for _ in range(n)],
        "up_token": "up", "dn_token": "dn",
    }
    return Win(d)


def flat_book(mid_up=0.50, n=1):
    """n ticks of a balanced book: up leg at mid_up, down leg at its complement."""
    return ([mid_up - 0.01] * n, [mid_up + 0.01] * n,
            [0.99 - mid_up - 0.01] * n, [0.99 - mid_up + 0.01] * n)


# ---------------------------------------------------------------------------
# dead-zone boundary (rule 8) — pct and sec units through the same helper
# ---------------------------------------------------------------------------

def test_pct_and_sec_boundaries_agree_on_same_seconds():
    assert book_math.dead_zone_cutoff_seconds(300, 0.10, "pct") == 30.0
    assert book_math.dead_zone_cutoff_seconds(300, 30, "sec") == 30.0
    # the lab's own wrapper must agree with the shared helper on both units
    assert lab.in_dead_zone(29.9, 300, 0.10, "pct") is True
    assert lab.in_dead_zone(30.1, 300, 0.10, "pct") is False
    assert lab.in_dead_zone(29.9, 300, 30, "sec") is True
    assert lab.in_dead_zone(30.1, 300, 30, "sec") is False


def test_sec_cutoff_is_absolute_not_relative():
    # 30 sec-unit seconds on a 900s window stays 30s; pct would say 90s
    assert book_math.dead_zone_cutoff_seconds(900, 30, "sec") == 30.0
    assert book_math.dead_zone_cutoff_seconds(900, 0.10, "pct") == 90.0


# ---------------------------------------------------------------------------
# fill timeline (issue #222 measurement 1)
# ---------------------------------------------------------------------------

def _stepped_window(duration=300, start=1000.0, n=301, tape_events=None):
    """Balanced 0.50 book every second; tape_events: {elapsed: (leg, price, side)}."""
    ts = [start + float(i) for i in range(n)]
    ubb, uba, dbb, dba = flat_book(0.50, n)
    tape = [([], []) for _ in range(n)]
    for elapsed, (leg, px, side) in (tape_events or {}).items():
        idx = int(elapsed)
        tick = ([(px, 10.0, side)], []) if leg == "up" else ([], [(px, 10.0, side)])
        tape[idx] = tick
    return make_win(ts, ubb, uba, dbb, dba, tape=tape, duration=duration,
                    start_ts=start)


def test_fill_times_measured_from_quote_placement():
    # resting up = 0.485, resting dn = 0.475 (two-sided mid 0.505, offset 0.02)
    w = _stepped_window(tape_events={
        60: ("up", 0.485, SIDE_SELL),   # print at our resting up bid
        120: ("dn", 0.475, SIDE_SELL),  # print at our resting dn bid
    })
    rec = lab.simulate_window_timeline(w)
    assert rec["entered"] is True
    assert rec["placed_elapsed"] == pytest.approx(0.0)
    assert rec["fill_up_sec"] == pytest.approx(60.0)
    assert rec["fill_dn_sec"] == pytest.approx(120.0)
    assert rec["time_to_pair_sec"] == pytest.approx(120.0)
    assert rec["outcome"] == "pair"


def test_quote_placement_tracks_first_in_range_tick():
    # first 31 ticks (t=0..30) the two-sided mid is 0.95 (outside quote_range)
    # -> placement waits for the first in-range tick at t=30... ticks are 1s
    # apart, so t=31 is the first in-range elapsed value after the 0..30 block.
    ts = [1000.0 + float(i) for i in range(301)]
    n = len(ts)
    ubb = [0.94] * 31 + [0.49] * (n - 31)
    uba = [0.96] * 31 + [0.51] * (n - 31)
    dbb = [0.04] * 31 + [0.49] * (n - 31)
    dba = [0.06] * 31 + [0.51] * (n - 31)
    w = make_win(ts, ubb, uba, dbb, dba, duration=300, start_ts=1000.0)
    rec = lab.simulate_window_timeline(w)
    assert rec["entered"] is True
    assert rec["placed_elapsed"] == pytest.approx(31.0)


def test_newly_placed_marketable_quote_fills_on_placement_tick():
    # An order placed at/above the current ask is marketable on arrival
    # (`newly_placed` branch of the shared rule): the placement-tick ask sits
    # below the resting price (up 0.39/0.40, dn 0.49/0.51 -> om 0.4475,
    # resting up 0.4275 >= ask 0.40), so the up quote fills on tick 0 itself.
    ts = [1000.0 + float(i) for i in range(301)]
    n = len(ts)
    ubb = [0.39] * n
    uba = [0.50] * n
    dbb = [0.49] * n
    dba = [0.51] * n
    uba[0] = 0.40  # marketable against resting 0.4275 on the placement tick only
    w = make_win(ts, ubb, uba, dbb, dba, duration=300, start_ts=1000.0)
    rec = lab.simulate_window_timeline(w)
    assert rec["fill_up_sec"] == pytest.approx(0.0)


def test_ask_through_resting_price_fills_without_tape():
    ts = [1000.0 + float(i) for i in range(301)]
    ubb, uba, dbb, dba = flat_book(0.50, 301)
    uba[90] = 0.484  # strictly below resting 0.485 (minus one tick)
    w = make_win(ts, ubb, uba, dbb, dba, duration=300, start_ts=1000.0)
    rec = lab.simulate_window_timeline(w)
    assert rec["fill_up_sec"] == pytest.approx(90.0)


def test_buy_print_never_fills_a_resting_bid():
    w = _stepped_window(tape_events={60: ("up", 0.485, SIDE_BUY)})
    rec = lab.simulate_window_timeline(w)
    assert rec["fill_up_sec"] is None
    assert rec["outcome"] == "no_fill"


def test_late_start_inside_dead_zone_is_excluded():
    # first tick is 295s into a 300s window: 5s remaining, inside the 30s dead zone
    ts = [1295.0 + float(i) for i in range(6)]
    ubb, uba, dbb, dba = flat_book(0.50, 6)
    w = make_win(ts, ubb, uba, dbb, dba, duration=300, start_ts=1000.0)
    rec = lab.simulate_window_timeline(w)
    assert rec["entered"] is False
    assert rec["excluded"] == "starts_in_dead_zone"


# ---------------------------------------------------------------------------
# naked-leg capture (issue #223 measurement)
# ---------------------------------------------------------------------------

def _naked_down_window(entry_elapsed=60.0, dz_up_mid=0.90, dn_bid_at_dz=0.19,
                       dn_ask_at_dz=0.21, final_mid_up=0.97, duration=300):
    """Down leg fills at `entry_elapsed`, never pairs, rides into the dead zone."""
    ts = [1000.0 + float(i) for i in range(301)]
    n = len(ts)
    ubb = [0.49] * n
    uba = [0.51] * n
    dbb = [0.49] * n
    dba = [0.51] * n
    tape = [([], []) for _ in range(n)]
    idx = int(entry_elapsed)
    tape[idx] = ([], [(0.48, 10.0, SIDE_SELL)])  # fills our dn bid
    dz_idx = 270  # dead zone starts at 270s remaining -> elapsed 270
    for i in range(dz_idx, n):
        ubb[i], uba[i] = dz_up_mid - 0.01, dz_up_mid + 0.01
        dbb[i], dba[i] = dn_bid_at_dz, dn_ask_at_dz
    # settle the proxy from the last two-sided mid
    ubb[-1], uba[-1] = final_mid_up - 0.01, final_mid_up + 0.01
    dbb[-1], dba[-1] = 0.99 - final_mid_up - 0.01, 0.99 - final_mid_up + 0.01
    return make_win(ts, ubb, uba, dbb, dba, tape=tape, duration=300,
                    start_ts=1000.0)


def test_naked_leg_reaches_dead_zone_with_price_and_bid():
    w = _naked_down_window()
    rec = lab.simulate_window_timeline(w)
    assert rec["outcome"] == "naked"
    assert rec["held_side"] == "dn"
    assert rec["entry_price"] == pytest.approx(0.48)
    assert rec["dz_elapsed"] == pytest.approx(270.0)
    assert rec["dz_leg_mid"] == pytest.approx(0.20)   # dn book 0.19/0.21
    assert rec["dz_leg_bid"] == pytest.approx(0.19)
    assert rec["won"] is False                        # last mid 0.97 -> up won


def test_settlement_proxy_ambiguity_is_none_not_a_guess():
    assert lab.settlement_won(0.60, held_up=True) is True
    assert lab.settlement_won(0.60, held_up=False) is False
    assert lab.settlement_won(0.40, held_up=False) is True
    assert lab.settlement_won(0.505, held_up=True) is None   # ambiguous band
    assert lab.settlement_won(None, held_up=True) is None    # no mid at all


def test_close_vs_hold_arithmetic_includes_taker_fee():
    # close at 0.20 after entering at 0.25: (0.20-0.25)*100 - fee(0.20)*100
    fee = 0.07 * 0.20 * 0.80 * 100.0
    close_net, hold_net = lab.close_hold_values(
        entry=0.25, bid=0.20, won=False, taker_fee_rate=0.07)
    assert close_net == pytest.approx(-5.0 - fee)
    assert hold_net == pytest.approx(-25.0)
    _, hold_win = lab.close_hold_values(
        entry=0.25, bid=0.20, won=True, taker_fee_rate=0.07)
    assert hold_win == pytest.approx(75.0)
    assert lab.close_hold_values(entry=0.25, bid=None, won=False,
                                 taker_fee_rate=0.07)[0] is None


def test_naked_leg_without_dz_book_is_flagged_not_valued():
    w = _naked_down_window(dn_bid_at_dz=None, dn_ask_at_dz=None)
    rec = lab.simulate_window_timeline(w)
    assert rec["outcome"] == "naked"
    assert rec["dz_leg_bid"] is None
    assert lab.close_hold_values(entry=rec["entry_price"], bid=None,
                                 won=rec["won"], taker_fee_rate=0.07)[0] is None


# ---------------------------------------------------------------------------
# bucketing + verdicts (issues #222 and #223 decision rules)
# ---------------------------------------------------------------------------

def test_bucket_edges_are_half_cent_wide_lower_inclusive():
    assert lab.bucket_of(0.25) == pytest.approx(0.25)
    assert lab.bucket_of(0.249) == pytest.approx(0.20)
    assert lab.bucket_of(0.999) == pytest.approx(0.95)
    assert lab.bucket_of(None) is None


def test_distribution_stats_report_spread_not_just_mean():
    s = lab.distribution_stats([1.0, 2.0, 3.0, 4.0])
    assert s["n"] == 4
    assert s["mean"] == pytest.approx(2.5)
    assert s["median"] == pytest.approx(2.5)
    assert s["std"] > 0
    # statistics.quantiles (n=4, default 'exclusive' method) on 1..4
    assert s["p25"] == pytest.approx(1.25)
    assert s["p75"] == pytest.approx(3.75)


def _fake_timeline(duration, n, ttp):
    """n naked/pair records for one duration with a fixed time-to-pair."""
    return [{"cid": f"c{i}", "duration": duration, "entered": True,
             "excluded": None, "time_to_pair_sec": ttp,
             "time_to_first_fill_sec": ttp / 2, "outcome": "pair"}
            for i in range(n)]


def test_222_verdict_sec_when_time_to_pair_constant_across_durations():
    recs = _fake_timeline(300, 12, 60.0) + _fake_timeline(900, 12, 60.0)
    v = lab.verdict_dead_zone_222(recs)
    assert v["verdict"] == "sec"
    assert v["median_ttp_5m"] == pytest.approx(60.0)
    assert v["median_ttp_15m"] == pytest.approx(60.0)
    assert v["ratio_15m_over_5m"] == pytest.approx(1.0)


def test_222_verdict_pct_when_dead_tail_scales_with_window():
    recs = _fake_timeline(300, 12, 60.0) + _fake_timeline(900, 12, 180.0)
    v = lab.verdict_dead_zone_222(recs)
    assert v["verdict"] == "pct"
    assert v["ratio_15m_over_5m"] == pytest.approx(3.0)


def test_222_verdict_inconclusive_on_thin_samples():
    recs = _fake_timeline(300, 3, 60.0) + _fake_timeline(900, 3, 60.0)
    assert lab.verdict_dead_zone_222(recs)["verdict"] == "inconclusive"


def _fake_naked(bucket, n, close_net, hold_net, bid_gap=0.02, settle_rate=0.2):
    return {"bucket": bucket, "n": n, "n_valued": n, "n_close_impossible": 0,
            "settlement_rate": settle_rate, "mean_leg_price": bucket + 0.025,
            "mean_dz_bid": bucket + 0.025 - bid_gap,
            "mean_bid_minus_mid": -bid_gap,
            "close_stats": lab.distribution_stats([close_net] * n),
            "hold_stats": lab.distribution_stats([hold_net] * n),
            "mean_close_minus_hold": close_net - hold_net}


def test_223_buckets_below_min_n_never_cited():
    buckets = {"0.20": _fake_naked(0.20, 5, -6.0, -25.0)}
    v = lab.verdict_naked_leg_223(buckets, total_naked=5)
    assert v["per_bucket"]["0.20"]["cited"] is False
    assert v["verdict"] == "inconclusive"


def test_223_close_wins_low_buckets_and_verdict_stays_close():
    # low buckets: closing beats holding (longshot bias); that is the default's side
    buckets = {"0.20": _fake_naked(0.20, 40, -6.0, -25.0),
               "0.30": _fake_naked(0.30, 40, -4.0, -20.0)}
    v = lab.verdict_naked_leg_223(buckets, total_naked=80)
    assert v["per_bucket"]["0.20"]["winner"] == "close"
    assert v["verdict"] == "close"


def test_223_hold_only_overturns_when_it_beats_close_by_more_than_a_cent():
    # hold ahead by 0.5c: the variance cost rules it out (rule 14's reasoning)
    buckets = {"0.80": _fake_naked(0.80, 40, 10.0, 10.5)}
    v = lab.verdict_naked_leg_223(buckets, total_naked=40)
    assert v["verdict"] == "close"
    # hold ahead by 2c: decisive, and the verdict says so
    buckets = {"0.80": _fake_naked(0.80, 40, 10.0, 12.0)}
    v = lab.verdict_naked_leg_223(buckets, total_naked=40)
    assert v["verdict"] == "hold"


def test_223_price_dependence_is_reported_when_low_and_high_disagree():
    buckets = {"0.20": _fake_naked(0.20, 40, -6.0, -25.0),
               "0.80": _fake_naked(0.80, 40, 10.0, 13.0)}
    v = lab.verdict_naked_leg_223(buckets, total_naked=80)
    assert v["price_dependent"] is True
    assert v["low_side"] == "close"
    assert v["high_side"] == "hold"


# ---------------------------------------------------------------------------
# dataset stamping / reproducibility guard
# ---------------------------------------------------------------------------

def test_artifact_header_stamps_inputs():
    hdr = lab.dataset_header(["run/ticks/ticks_2026-09-14.jsonl"],
                             line_counts={"run/ticks/ticks_2026-09-14.jsonl": 509402})
    assert hdr["files"] == ["run/ticks/ticks_2026-09-14.jsonl"]
    assert hdr["lines"]["run/ticks/ticks_2026-09-14.jsonl"] == 509402
    assert "params" in hdr and hdr["params"]["offset"] == lab.DEFAULT_OFFSET
