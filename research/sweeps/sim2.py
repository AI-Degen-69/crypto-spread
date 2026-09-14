"""sim2: research simulator extensions on top of ev_lab's window cache.

Adds two mechanics the canonical engine does not model (default args preserve
engine parity for fill_model="tape"):

  1. fill_model="tapeq" - queue-aware tape fills:
     - queue ahead is snapshotted when a leg becomes quotable,
     - burned down by printed trade SIZE at our price,
     - any sell print strictly THROUGH our price fills us regardless of queue,
     - buy-side prints (side=1, classified in the cache from the snapshot book)
       cannot fill a resting bid.

  2. chase_cap - leg-chase pairs rule (mirrors live issue #123):
     after exactly one leg fills, the opposite quote is re-anchored each tick
     to min(ask, chase_cap - entry) (never lowered), and fills on any sell
     print at or below it, converting the naked leg into a pair at <= cap.

Rows returned are compatible with ev_lab.summarize().
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams  # noqa: E402
from ev_lab import (  # noqa: E402
    Win, _mid_from, _two_sided, _queue_ahead, _taker_fee,
    reject_knobs_sim2_ignores,
)


def sim2(w: Win, p: BacktestParams, chase_cap: float | None = None,
         entry_delay_sec: float = 0.0, entry_band: float | None = None) -> dict:
    """Simulate one cached window with queue-aware fills and optional leg chase.

    `entry_delay_sec > 0` delays quoting until that much of the window has
    elapsed (research knob: lets the opening queue drain and the adverse open
    resolve; classification uses the full path, quoting the delayed part).

    `entry_band` (research knob): at the first quoted tick, skip the window
    entirely unless |two-sided mid - 0.50| <= entry_band (undecided-market
    regime filter; stronger than the adverse-open gate, which uses exit_thr).

    Both are read from these arguments, never from `p` — so a caller that sets
    them on the `BacktestParams` instead is rejected rather than quietly
    simulated without them.
    """
    reject_knobs_sim2_ignores(p)
    duration = w.duration
    start_ts = w.start_ts
    first_ts = w.first_ts
    raw_delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
    start_delay_sec = round(raw_delay, 2)
    late_start = bool(
        p.max_start_elapsed_pct and p.max_start_elapsed_pct > 0
        and duration > 0 and raw_delay >= p.max_start_elapsed_pct * duration
    )

    exit_thr = p.exit_thresh(w.slug, duration, series=w.series)

    # anchor resting quotes at the first observed mid AT/AFTER the entry delay
    init_mid = None
    for k, m in enumerate(w.s_mid):
        if m is None:
            continue
        tk = w.ts[k]
        el = max(0.0, tk - start_ts) if (tk > 0.0 and start_ts > 0.0) else float(k)
        if entry_delay_sec <= 0 or el >= entry_delay_sec:
            init_mid = float(m)
            break
    if init_mid is None:
        init_mid = 0.50
    resting_up = round(min(0.99, max(0.01, init_mid - p.offset)), 3)
    resting_dn = round(min(0.99, max(0.01, (1.0 - init_mid) - p.offset)), 3)

    filled_up = filled_dn = False
    entry_cancelled = late_start
    adverse_skipped = False
    gate_evaluated = False
    reentry_count = 0
    reversal_up = reversal_dn = False
    pair = exit_taken = False
    exit_side = ""
    max_up = max_dn = 0.0
    n_mids = 0
    pnl = fees = 0.0
    cap_up = cap_dn = 0.0

    fm = p.fill_model
    tq = fm == "tapeq"
    q_up = q_dn = None
    q_rest_up = q_rest_dn = None
    chased_leg = ""

    timeout_on = p.entry_timeout_pct is not None and p.entry_timeout_pct > 0 and duration > 0
    if timeout_on and raw_delay >= p.entry_timeout_pct * duration:
        entry_cancelled = True

    chase_on = chase_cap is not None and chase_cap > 0
    n = len(w.ts)
    for i in range(n):
        cur_ts = w.ts[i]
        elapsed = max(0.0, cur_ts - start_ts) if (cur_ts > 0.0 and start_ts > 0.0) else float(i)

        if timeout_on and not entry_cancelled:
            if elapsed > (p.entry_timeout_pct * duration):
                if not filled_up and not filled_dn:
                    entry_cancelled = True

        mid = _mid_from(w.up_bb[i], w.up_ba[i])
        if mid is None:
            continue
        n_mids += 1
        if mid - 0.50 > max_up:
            max_up = mid - 0.50
        if 0.50 - mid > max_dn:
            max_dn = 0.50 - mid

        # delayed entry: observe-only until the delay elapses
        if entry_delay_sec > 0 and elapsed < entry_delay_sec:
            continue

        if max_dn >= exit_thr and (0.50 - mid) < p.exit_reversal:
            reversal_dn = True
        if max_up >= exit_thr and (mid - 0.50) < p.exit_reversal:
            reversal_up = True

        if not gate_evaluated and not late_start:
            om = _two_sided(w.up_bb[i], w.up_ba[i], w.dn_bb[i], w.dn_ba[i])
            if om is not None:
                gate_evaluated = True
                if entry_band is not None and entry_band > 0:
                    # regime filter: quote only undecided markets
                    if abs(om - 0.50) > entry_band:
                        entry_cancelled = True
                elif abs(om - 0.50) >= exit_thr:
                    entry_cancelled = True
                    adverse_skipped = True

        if (adverse_skipped and not filled_up and not filled_dn
                and reentry_count < p.max_reentries_per_window):
            remaining = max(0.0, duration - elapsed) if duration > 0 else 0.0
            min_remaining = p.min_requote_remaining_sec
            if duration > 0 and p.reentry_min_remaining_pct is not None \
                    and 0.0 < p.reentry_min_remaining_pct <= 1.0:
                min_remaining = min(min_remaining, p.reentry_min_remaining_pct * duration)
            rm = _two_sided(w.up_bb[i], w.up_ba[i], w.dn_bb[i], w.dn_ba[i])
            drift = abs(rm - 0.50) if rm is not None else None
            if (rm is not None and p.reentry_drift_band is not None
                    and p.reentry_drift_band > 0
                    and remaining >= min_remaining
                    and drift <= min(p.reentry_drift_band, exit_thr)
                    and drift < exit_thr):
                entry_cancelled = False
                adverse_skipped = False
                reentry_count += 1
                r_mid = w.s_mid[i] if w.s_mid[i] is not None else rm
                if not filled_up:
                    resting_up = round(min(0.99, max(0.01, r_mid - p.offset)), 3)
                    q_up = None
                if not filled_dn:
                    resting_dn = round(min(0.99, max(0.01, (1.0 - r_mid) - p.offset)), 3)
                    q_dn = None

        if p.queue_gate is not None and p.queue_gate > 0:
            qa_up = _queue_ahead(w.up_bids[i], resting_up)
            qa_dn = _queue_ahead(w.dn_bids[i], resting_dn)
            queue_ok = (qa_up <= p.queue_gate) and (qa_dn <= p.queue_gate)
        else:
            queue_ok = True

        up_ask = w.up_ba[i]
        dn_ask = w.dn_ba[i]
        if p.pair_cost_gate is not None and p.pair_cost_gate > 0:
            if up_ask is not None and dn_ask is not None:
                pair_cost_ok = (up_ask + dn_ask) <= p.pair_cost_gate
            else:
                pair_cost_ok = True
        else:
            pair_cost_ok = True

        if not queue_ok or not pair_cost_ok:
            if (filled_up and not filled_dn and max_dn >= exit_thr
                    and not reversal_dn and not exit_taken):
                bb = w.up_bb[i]
                if bb is not None:
                    exit_taken, exit_side = True, "up"
                    pnl += (bb - resting_up) * 100.0
                    fees += _taker_fee(bb, p.taker_fee_rate) * 100.0
                    break
            if (filled_dn and not filled_up and max_up >= exit_thr
                    and not reversal_up and not exit_taken):
                bb = w.dn_bb[i]
                if bb is not None:
                    exit_taken, exit_side = True, "down"
                    pnl += (bb - resting_dn) * 100.0
                    fees += _taker_fee(bb, p.taker_fee_rate) * 100.0
                    break
            if not filled_up and not filled_dn:
                continue

        # --- leg chase (issue #123): re-anchor the unfilled leg each tick ---
        if chase_on and (filled_up != filled_dn) and not pair and not exit_taken:
            if filled_up:
                cap_px = round(min(0.99, math.floor((chase_cap - resting_up + 1e-9) * 100.0) / 100.0), 3)
                target = min(dn_ask, cap_px) if dn_ask is not None else cap_px
                if target > resting_dn:
                    resting_dn = target
                    q_dn = None
                chased_leg = "dn"
            else:
                cap_px = round(min(0.99, math.floor((chase_cap - resting_dn + 1e-9) * 100.0) / 100.0), 3)
                target = min(up_ask, cap_px) if up_ask is not None else cap_px
                if target > resting_up:
                    resting_up = target
                    q_up = None
                chased_leg = "up"

        can_up = (not filled_up) and (not entry_cancelled or filled_dn)
        can_dn = (not filled_dn) and (not entry_cancelled or filled_up)
        if tq:
            if can_up and q_up is None and not entry_cancelled and chased_leg != "up":
                q_rest_up = resting_up
                q_up = _queue_ahead(w.up_bids[i], resting_up)
            if can_dn and q_dn is None and not entry_cancelled and chased_leg != "dn":
                q_rest_dn = resting_dn
                q_dn = _queue_ahead(w.dn_bids[i], resting_dn)
            # book-through: ask strictly through our resting price
            if can_up and up_ask is not None and up_ask <= (resting_up - p.tick_size + 1e-6):
                filled_up, can_up = True, False
            if can_dn and dn_ask is not None and dn_ask <= (resting_dn - p.tick_size + 1e-6):
                filled_dn, can_dn = True, False

        tup_now, tdn_now = w.tape[i]
        if can_up and fm in ("tape", "both", "cross", "tapeq"):
            for tr in tup_now:
                tpx, tsz = tr[0], tr[1]
                tside = tr[2] if len(tr) > 2 else 0
                if fm in ("tape", "both"):
                    if abs(tpx - resting_up) <= (p.tick_size + 1e-6):
                        filled_up, can_up = True, False
                        break
                elif fm == "cross":
                    if tpx <= (resting_up - p.tick_size + 1e-6):
                        filled_up, can_up = True, False
                        break
                else:  # tapeq
                    if tside == 1:
                        continue  # buy lifted the ask; cannot fill our bid
                    if chased_leg == "up":
                        if tpx <= resting_up + 1e-9:
                            filled_up, can_up = True, False
                            break
                        continue
                    if q_rest_up is not None and tpx <= (q_rest_up - p.tick_size + 1e-6):
                        filled_up, can_up = True, False
                        break
                    if q_rest_up is not None and abs(tpx - q_rest_up) <= (p.tick_size + 1e-6):
                        if q_up is None or q_up - tsz <= 0.0:
                            filled_up, can_up = True, False
                            break
                        q_up -= tsz
        if can_dn and fm in ("tape", "both", "cross", "tapeq"):
            for tr in tdn_now:
                tpx, tsz = tr[0], tr[1]
                tside = tr[2] if len(tr) > 2 else 0
                if fm in ("tape", "both"):
                    if abs(tpx - resting_dn) <= (p.tick_size + 1e-6):
                        filled_dn, can_dn = True, False
                        break
                elif fm == "cross":
                    if tpx <= (resting_dn - p.tick_size + 1e-6):
                        filled_dn, can_dn = True, False
                        break
                else:  # tapeq
                    if tside == 1:
                        continue
                    if chased_leg == "dn":
                        if tpx <= resting_dn + 1e-9:
                            filled_dn, can_dn = True, False
                            break
                        continue
                    if q_rest_dn is not None and tpx <= (q_rest_dn - p.tick_size + 1e-6):
                        filled_dn, can_dn = True, False
                        break
                    if q_rest_dn is not None and abs(tpx - q_rest_dn) <= (p.tick_size + 1e-6):
                        if q_dn is None or q_dn - tsz <= 0.0:
                            filled_dn, can_dn = True, False
                            break
                        q_dn -= tsz
        if fm in ("book", "both"):
            if can_up and up_ask is not None and up_ask <= resting_up:
                filled_up = True
            if can_dn and dn_ask is not None and dn_ask <= resting_dn:
                filled_dn = True
        elif fm == "cross":
            if can_up and up_ask is not None and up_ask <= (resting_up - p.tick_size + 1e-6):
                filled_up = True
            if can_dn and dn_ask is not None and dn_ask <= (resting_dn - p.tick_size + 1e-6):
                filled_dn = True

        if filled_up and filled_dn and not pair and not exit_taken:
            pair = True
            pnl += (1.00 - (resting_up + resting_dn)) * 100.0
            pnl -= (p.merge_gas_usd * 100.0) / max(1, p.quote_shares)
            cap_up, cap_dn = resting_up * 100.0, resting_dn * 100.0
            break

        if (filled_up and not filled_dn and max_dn >= exit_thr
                and not reversal_dn and not exit_taken):
            bb = w.up_bb[i]
            if bb is not None:
                exit_taken, exit_side = True, "up"
                pnl += (bb - resting_up) * 100.0
                fees += _taker_fee(bb, p.taker_fee_rate) * 100.0
                cap_up = resting_up * 100.0
                break
        if (filled_dn and not filled_up and max_up >= exit_thr
                and not reversal_up and not exit_taken):
            bb = w.dn_bb[i]
            if bb is not None:
                exit_taken, exit_side = True, "down"
                pnl += (bb - resting_dn) * 100.0
                fees += _taker_fee(bb, p.taker_fee_rate) * 100.0
                cap_dn = resting_dn * 100.0
                break

        if timeout_on and not entry_cancelled:
            if elapsed >= (p.entry_timeout_pct * duration):
                if not filled_up and not filled_dn:
                    entry_cancelled = True

    if filled_up:
        cap_up = resting_up * 100.0
    if filled_dn:
        cap_dn = resting_dn * 100.0

    held_side = ""
    naked_none = False
    settle_won = None
    settle_delta = 0.0
    if filled_up or filled_dn:
        if (filled_up and not filled_dn) or (filled_dn and not filled_up):
            fees += _taker_fee(0.50, p.taker_fee_rate) * 100.0
        if not pair and not exit_taken:
            held_side = "up" if filled_up else "down"
            lb = w.up_bb[-1]
            db_bid = w.dn_bb[-1]
            if filled_up and not filled_dn and lb is not None:
                pnl += (lb - resting_up) * 100.0
                fees += _taker_fee(lb, p.taker_fee_rate) * 100.0
            elif filled_dn and not filled_up and db_bid is not None:
                pnl += (db_bid - resting_dn) * 100.0
                fees += _taker_fee(db_bid, p.taker_fee_rate) * 100.0
            elif (filled_up and not filled_dn and lb is None) or \
                 (filled_dn and not filled_up and db_bid is None):
                naked_none = True
                resting = resting_up if held_side == "up" else resting_dn
                if held_side == "up":
                    ref = _mid_from(w.up_bb[-1], w.up_ba[-1])
                    if ref is None:
                        dm = _mid_from(w.dn_bb[-1], w.dn_ba[-1])
                        ref = (1.0 - dm) if dm is not None else None
                else:
                    ref = _mid_from(w.dn_bb[-1], w.dn_ba[-1])
                    if ref is None:
                        um = _mid_from(w.up_bb[-1], w.up_ba[-1])
                        ref = (1.0 - um) if um is not None else None
                if ref is not None and ref != 0.5:
                    settle_won = ref > 0.5
                    settle_delta = (1.0 - resting) * 100.0 if settle_won else (-resting) * 100.0

    if n_mids == 0:
        cls = "no_data"
    elif max_up >= 0.02 and max_dn >= 0.02:
        cls = "oscillating"
    elif max_up >= 0.02 or max_dn >= 0.02:
        cls = "monotonic"
    else:
        cls = "flat"

    return {
        "cid": w.cid, "series": w.series, "slug": w.slug, "duration": duration,
        "day": w.day, "n_snaps": n, "class_label": cls,
        "max_up": round(max_up, 4), "max_dn": round(max_dn, 4),
        "filled_up": filled_up, "filled_dn": filled_dn,
        "pair": pair, "exit": exit_taken, "exit_side": exit_side,
        "pnl": round(pnl, 4), "fees": round(fees, 4),
        "capital": (cap_up + cap_dn) if (filled_up or filled_dn) else 0.0,
        "start_delay_sec": start_delay_sec,
        "reentry_count": reentry_count,
        "held_side": held_side, "naked_none": naked_none,
        "settle_won": settle_won, "settle_delta": round(settle_delta, 4),
        "chased": chased_leg,
    }
