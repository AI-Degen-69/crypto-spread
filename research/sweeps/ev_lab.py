"""EV research lab for SPREAD-2: window cache, fast parity simulator, statistics.

Goal: find (series x timeframe x params) combos whose mean per-window PnL has a
95% bootstrap CI lower bound above 0, using ALL collected tick data, with
per-day out-of-sample validation.

Pipeline:
  1. build_cache() parses run/ticks/*.jsonl once into compact per-window
     records (interleaved array('d') books, float lists) ~10x smaller than the
     raw dicts, so hundreds of configs can sweep the full dataset in RAM.
  2. fast_simulate() reproduces backtest.engine._simulate_window exactly for
     every parameter axis we sweep. `parity` mode verifies tick-for-tick
     equality (pnl, fees, flags) against the canonical engine.
  3. sweep infra + bootstrap statistics (window bootstrap + day-cluster
     bootstrap) for the 95% CI on mean PnL.

CLI:
    python research/sweeps/ev_lab.py cache                 # build window cache
    python research/sweeps/ev_lab.py parity [file-substr]  # fast sim == engine check
    python research/sweeps/ev_lab.py check '<config-json>' # one config, full stats
"""
from __future__ import annotations

import gzip
import json
import math
import pickle
import random
import statistics
import sys
import time
import zlib
from array import array
from dataclasses import MISSING, fields, replace
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from strategy.book_math import resting_bid_filled  # noqa: E402
from backtest.engine import (  # noqa: E402
    BacktestParams,
    _mid,
    _simulate_window,
    group_by_cid,
    iter_ticks,
    resolve_redemption,
)

TICKS_DIR = ROOT / "run" / "ticks"
CACHE_PATH = ROOT / "run" / "sweeps" / "window_cache.pkl"
BOOK_MIN_PX = 0.05  # queue sums only ever count bids >= resting (>= 0.05 in practice)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

class Win:
    """One condition window (dict-backed so the pickle is class-free and
    safe to unpickle in spawned worker processes)."""
    _KEYS = ("cid", "series", "slug", "duration", "start_ts", "day",
             "ts", "s_mid", "up_bb", "up_ba", "dn_bb", "dn_ba",
             "up_bids", "dn_bids", "tape", "up_token", "dn_token")

    def __init__(self, d: dict):
        for k in self._KEYS:
            setattr(self, k, d[k])

    @property
    def first_ts(self):
        return self.ts[0] if self.ts else 0.0


#: Bumped whenever the cached record shape changes, so `load_cache` rebuilds
#: instead of silently feeding an old pickle to code expecting the new shape.
CACHE_VERSION = 2

SIDE_SELL = 0
SIDE_BUY = 1


def _classify_side(px: float, best_bid, best_ask) -> int:
    """Classify one print as buy (1) or sell (0) from the snapshot book.

    Issue #182: `sim2` documents and consumes a third tuple element for this,
    but `build_cache` only ever stored `(px, sz)` — so `tr[2] if len(tr) > 2
    else 0` made every print look like a sell, and a buy that lifted the ask
    could fill our resting bid. Both research simulators now filter on this
    before the shared fill rule sees a print (issue #226).

    A print at or above the ask is the aggressor lifting it (buy); at or below
    the bid is the aggressor hitting it (sell). Inside an untouched spread it
    is unattributable, and `SIDE_SELL` is the conservative answer for a resting
    *bid*: it keeps the print eligible to fill us, matching the pre-existing
    default rather than quietly making fills rarer.
    """
    try:
        if best_ask is not None and px >= float(best_ask) - 1e-9:
            return SIDE_BUY
        if best_bid is not None and px <= float(best_bid) + 1e-9:
            return SIDE_SELL
    except (TypeError, ValueError):
        return SIDE_SELL
    return SIDE_SELL


def _compact_bids(d: dict) -> array:
    """Book bids dict -> interleaved [p0, sz0, p1, sz1, ...] sorted desc."""
    out = array("d")
    if not d:
        return out
    items = sorted(((float(p), float(sz)) for p, sz in d.items()),
                   key=lambda kv: -kv[0])
    for p, sz in items:
        if p >= BOOK_MIN_PX:
            out.append(p)
            out.append(sz)
    return out


def build_cache(force: bool = False) -> Path:
    """Compact every tick file into per-window arrays, one pass, one snap at a time.

    The two-pass shape this replaces parsed a whole day into ``raw[cid] ->
    [snapshot dicts]`` before compacting any of it. Measured on this hardware the
    parsed dicts cost **5.5x the file size in RSS** (490MB file -> 2.67GB peak),
    so a 1GB day needed ~6GB of headroom and a 24h capture had none. Compacting
    each snapshot as it is read drops the raw dict immediately and leaves only
    the array-backed windows resident -- which the old shape held anyway, on top
    of ``raw``.

    Per-cid timestamps arrive ascending (the collector appends on one thread in
    series order), so the old per-cid ``sort`` was a no-op: measured 0
    regressions over 503,752 snapshots. Ascending order is asserted rather than
    assumed, because misaligned parallel arrays would corrupt every downstream
    simulation silently.
    """
    if CACHE_PATH.exists() and not force:
        print(f"cache exists: {CACHE_PATH} (use force=True to rebuild)")
        return CACHE_PATH
    files = sorted(set(TICKS_DIR.glob("ticks_*.jsonl")) | set(TICKS_DIR.glob("ticks_*.jsonl.gz")))
    windows: list[dict] = []
    t0 = time.perf_counter()
    for path in files:
        day = path.stem.replace(".jsonl", "").replace("ticks_", "")
        # Insertion-ordered by first appearance of each cid, which is the order
        # the previous `raw.items()` loop emitted.
        acc: dict[str, dict] = {}
        op = gzip.open if path.suffix == ".gz" else open
        with op(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                cid = s.get("cid")
                if not cid:
                    continue
                ub = s.get("up_book") or {}
                db = s.get("down_book") or {}
                w = acc.get(cid)
                if w is None:
                    w = acc[cid] = {
                        "cid": cid, "series": s.get("series", ""),
                        "slug": s.get("slug", ""),
                        "duration": int(s.get("duration", 0) or 0),
                        "start_ts": float(s.get("start_ts", 0.0) or 0.0),
                        "day": day,
                        "up_token": (s.get("up_token") or ub.get("token_id") or "").strip(),
                        "dn_token": (s.get("down_token") or db.get("token_id") or "").strip(),
                        "ts": [], "s_mid": [], "up_bb": [], "up_ba": [],
                        "dn_bb": [], "dn_ba": [], "up_bids": [], "dn_bids": [],
                        "tape": [],
                    }
                up_token, dn_token = w["up_token"], w["dn_token"]
                t = float(s.get("ts", 0.0) or 0.0)
                if w["ts"] and t < w["ts"][-1]:
                    raise ValueError(
                        f"{path.name}: cid {cid} timestamp went backwards "
                        f"({t} after {w['ts'][-1]}); the tick file is no longer "
                        "per-cid ascending and the parallel arrays would be misaligned")
                w["ts"].append(t)
                m = s.get("mid")
                w["s_mid"].append(None if m is None else float(m))
                ubb, uba = ub.get("best_bid"), ub.get("best_ask")
                dbb, dba = db.get("best_bid"), db.get("best_ask")
                w["up_bb"].append(None if ubb is None else float(ubb))
                w["up_ba"].append(None if uba is None else float(uba))
                w["dn_bb"].append(None if dbb is None else float(dbb))
                w["dn_ba"].append(None if dba is None else float(dba))
                w["up_bids"].append(_compact_bids(ub.get("bids") or {}))
                w["dn_bids"].append(_compact_bids(db.get("bids") or {}))
                tup, tdn = [], []
                for tr in s.get("tape_delta") or []:
                    a = str(tr.get("asset", "")).strip()
                    if not a:
                        continue
                    try:
                        px = float(tr.get("price", 0))
                        sz = float(tr.get("size", 0) or 0)
                    except Exception:
                        continue
                    if up_token and a == up_token:
                        tup.append((px, sz, _classify_side(px, ubb, uba)))
                    elif dn_token and a == dn_token:
                        tdn.append((px, sz, _classify_side(px, dbb, dba)))
                w["tape"].append((tup, tdn))
        windows.extend(acc.values())
        print(f"  {path.name}: {len(acc)} windows ({time.perf_counter()-t0:.0f}s elapsed)")
        acc.clear()
    windows.sort(key=lambda w: w["ts"][0] if w["ts"] else 0.0)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "wb") as f:
        pickle.dump({"version": CACHE_VERSION, "windows": windows}, f,
                    protocol=pickle.HIGHEST_PROTOCOL)
    print(f"cached {len(windows)} windows (v{CACHE_VERSION}) -> {CACHE_PATH} "
          f"({CACHE_PATH.stat().st_size/1e6:.0f}MB, {time.perf_counter()-t0:.0f}s)")
    return CACHE_PATH


def load_cache() -> list[Win]:
    """Load the window cache, rebuilding it when the on-disk shape is stale.

    Issue #182: the cache used to be a bare list with no version, so a pickle
    written before trade sides were stored would load fine and every print
    would read as a sell. A stale cache has to be rebuilt, not accepted.
    """
    if not CACHE_PATH.exists():
        build_cache()
    with open(CACHE_PATH, "rb") as f:
        raw = pickle.load(f)
    version = raw.get("version") if isinstance(raw, dict) else None
    if version != CACHE_VERSION:
        print(f"window cache is v{version}, need v{CACHE_VERSION} — rebuilding")
        build_cache(force=True)
        with open(CACHE_PATH, "rb") as f:
            raw = pickle.load(f)
        if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
            raise RuntimeError(
                f"window cache at {CACHE_PATH} is still not v{CACHE_VERSION} "
                "after a forced rebuild")
    return [w if isinstance(w, Win) else Win(w) for w in raw["windows"]]


# --------------------------------------------------------------------------
# Fast simulator (exact parity with engine._simulate_window)
# --------------------------------------------------------------------------

def _queue_ahead(book: array, resting: float) -> float:
    """Sum bid sizes at price >= resting (book interleaved, sorted desc)."""
    q = 0.0
    for i in range(0, len(book), 2):
        if book[i] >= resting:
            q += book[i + 1]
        else:
            break
    return q


def _mid_from(bb, ba):
    """engine._mid semantics from cached best_bid/best_ask fields."""
    if bb is not None and ba is not None:
        return (bb + ba) / 2.0
    if bb is not None:
        return bb + 0.005
    if ba is not None:
        return ba - 0.005
    return None


def _two_sided(ubb, uba, dbb, dba):
    if ubb is None or uba is None or dbb is None or dba is None:
        return None
    return round(((ubb + uba) / 2.0 + (1.0 - (dbb + dba) / 2.0)) / 2.0, 4)


def _taker_fee(p: float, rate: float) -> float:
    if p is None or p <= 0 or p >= 1:
        return 0.0
    return rate * p * (1.0 - p)


#: Fields `engine._simulate_window` honours that neither research simulator
#: implements. Issue #164 added all four to `BacktestParams`; measured on 400
#: real windows, flipping any one of them changes nothing in `sim2`'s output.
#: Issue #229: the engine drops dead-zone-blocked windows via `book_math
#: .is_in_dead_zone` on `dead_zone_val`/`dead_zone_unit`, and exits an
#: unpaired leg per `naked_leg_at_expiry` — neither simulator here does.
ENGINE_ONLY_KNOBS = ("exit_thresh_naked", "enable_leg_chase",
                     "dead_zone_val", "dead_zone_unit", "naked_leg_at_expiry")

#: Fields `engine._simulate_window` honours that `fast_simulate` does not.
#: The first three are implemented by `sim2` as its own call arguments, so
#: setting them on the params is silent rather than unimplemented --
#: `max_pair_cost` is `sim2`'s `chase_cap` argument (issue #227).
UNSUPPORTED_KNOBS = ("entry_delay_sec", "quote_range",
                     "max_pair_cost") + ENGINE_ONLY_KNOBS


def _non_default_knobs(p: BacktestParams, names: tuple[str, ...]) -> list[str]:
    """Which of `names` `p` sets away from its dataclass default.

    Compared against the declared default rather than tested for truthiness,
    because `enable_leg_chase` is a bool and `dead_zone_val=0.0` — the disable
    value — is a legitimate non-default: a truthiness test would wave through
    exactly the configurations that change what is being simulated.
    """
    defaults = {}
    for f in fields(BacktestParams):
        if f.default is not MISSING:
            defaults[f.name] = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            # `exit_thresh_by_slug` is one today. Without this branch `f.default`
            # is the MISSING sentinel, which never equals the real dict — so
            # adding such a field to the tuples would raise on every call,
            # including at its own default.
            defaults[f.name] = f.default_factory()  # type: ignore[misc]
    return [k for k in names
            if k in defaults and getattr(p, k, defaults[k]) != defaults[k]]


def _reject_unsupported_knobs(p: BacktestParams) -> None:
    """Raise when `p` sets a knob `fast_simulate` would silently ignore."""
    ignored = _non_default_knobs(p, UNSUPPORTED_KNOBS)
    if ignored:
        raise ValueError(
            f"fast_simulate does not implement {', '.join(ignored)}; "
            "engine._simulate_window applies them, so results would not be "
            "comparable. Use research/sweeps/sim2.py for entry_delay_sec, "
            "quote_range and max_pair_cost (its `chase_cap` argument); nothing "
            f"in research/ implements {', '.join(ENGINE_ONLY_KNOBS)}."
        )


def reject_knobs_sim2_ignores(p: BacktestParams) -> None:
    """Raise when `p` sets a knob `sim2` would silently ignore.

    `sim2` takes `entry_delay_sec`, `quote_range` and the chase ceiling as its
    own arguments -- the ceiling under the name `chase_cap` -- and never reads
    the `BacktestParams` fields behind them, so setting one on the params is as
    silent as not implementing it at all. The `ENGINE_ONLY_KNOBS` it does not
    implement in any form.
    """
    ignored = _non_default_knobs(p, UNSUPPORTED_KNOBS)
    if ignored:
        raise ValueError(
            f"sim2 ignores {', '.join(ignored)} on BacktestParams; pass "
            "entry_delay_sec / quote_range / max_pair_cost (as `chase_cap`) "
            f"as sim2() arguments, and note that {', '.join(ENGINE_ONLY_KNOBS)} "
            "are not implemented in research/ at all — the dead zone and the "
            "naked-leg-at-expiry policy are engine-only (issue #229)."
        )


def fast_simulate(w: Win, p: BacktestParams) -> dict:
    """Mirror engine._simulate_window for one cached window.

    Raises on a parameter this simulator does not implement. `entry_delay_sec`
    and `entry_band` are applied by `engine._simulate_window` before entry but
    have never been implemented here, and the parity parameter sets never
    exercised them — so the "0 mismatches across 6,840 window-checks" claim
    silently did not cover the two knobs that define `patient_band_maker`.
    Ignoring them returned plausible numbers for a strategy that was never
    simulated; failing loudly is the only safe behaviour (issue #182). The
    `sim2` research extensions do implement both — use those.
    """
    _reject_unsupported_knobs(p)
    duration = w.duration
    start_ts = w.start_ts
    first_ts = w.first_ts
    raw_delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
    start_delay_sec = round(raw_delay, 2)
    is_partial = bool(raw_delay > 5.0)
    # Issue #229: the deleted `max_start_elapsed_pct` gate is gone; the dead
    # zone (`ENGINE_ONLY_KNOBS`) owns the tail of the window and is rejected
    # above rather than simulated, so late-start handling is none of ours.

    exit_thr = p.exit_thresh(w.slug, duration, series=w.series)

    # anchor resting quotes at the first observed mid (engine init loop)
    init_mid = None
    for m in w.s_mid:
        if m is not None:
            init_mid = float(m)
            break
    if init_mid is None:
        for i in range(len(w.ts)):
            u_m = _mid_from(w.up_bb[i], w.up_ba[i])
            if u_m is not None:
                init_mid = float(u_m)
                break
    if init_mid is None:
        init_mid = 0.50
    resting_up = round(min(0.99, max(0.01, init_mid - p.offset)), 3)
    resting_dn = round(min(0.99, max(0.01, (1.0 - init_mid) - p.offset)), 3)

    filled_up = filled_dn = False
    entry_cancelled = False
    orders_live = False
    adverse_skipped = False
    gate_evaluated = False
    reentry_count = 0
    reversal_up = reversal_dn = False
    pair = exit_taken = False
    exit_side = ""
    max_up = max_dn = 0.0
    n_mids = 0
    pnl = fees = 0.0
    cap_up = cap_dn = 0.0  # capital deployed (cents) per leg

    n = len(w.ts)
    for i in range(n):
        requoted_now = False
        cur_ts = w.ts[i]
        elapsed = max(0.0, cur_ts - start_ts) if (cur_ts > 0.0 and start_ts > 0.0) else float(i)

        mid = _mid_from(w.up_bb[i], w.up_ba[i])
        if mid is None:
            continue
        n_mids += 1
        if mid - 0.50 > max_up:
            max_up = mid - 0.50
        if 0.50 - mid > max_dn:
            max_dn = 0.50 - mid

        if max_dn >= exit_thr and (0.50 - mid) < p.exit_reversal:
            reversal_dn = True
        if max_up >= exit_thr and (mid - 0.50) < p.exit_reversal:
            reversal_up = True

        # Issue #228: per-tick quote_range replaces adverse_open and re-entry
        om = _two_sided(w.up_bb[i], w.up_ba[i], w.dn_bb[i], w.dn_ba[i])
        if not orders_live and (om is None or not (p.quote_range[0] <= om <= p.quote_range[1])):
            continue

        if p.queue_gate is not None and p.queue_gate > 0:
            qa_up = _queue_ahead(w.up_bids[i], resting_up)
            qa_dn = _queue_ahead(w.dn_bids[i], resting_dn)
            queue_ok = (qa_up <= p.queue_gate) and (qa_dn <= p.queue_gate)
        else:
            queue_ok = True

        up_ask = w.up_ba[i]
        dn_ask = w.dn_ba[i]
        # Issue #227 deleted the entry-side pair-cost test that stood here. It
        # compared the book's two asks against the cap, and the two asks of a
        # binary pair always sum to roughly 1.00-1.01, so it carried no
        # information about the market. `max_pair_cost` caps the leg chase and
        # nothing else.

        if not queue_ok:
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

        can_up = (not filled_up) and (not entry_cancelled or filled_dn)
        can_dn = (not filled_dn) and (not entry_cancelled or filled_up)
        # --- fill detection: the one rule (issue #226) ---
        # `book_math.resting_bid_filled`, the same call the canonical engine
        # makes. The four selectable models -- and `tapeq`, a queue-aware
        # model with no engine equivalent at all -- went with the knob:
        # how a venue fills you is not a research variable (ADR-0002). The
        # queue question tapeq was built to probe still has `queue_gate` and
        # `_queue_ahead`.
        #
        # Prints are pre-filtered to sells: a buy that lifted the ask cannot
        # fill our resting bid (issue #182). The shared rule does not know a
        # print's side, so the caller that does decides which are ours.
        #
        # A quote not yet on the book is being placed on this tick and can be
        # marketable on arrival; one already resting waits for the ask to pass
        # fully through it.
        placed_now = (not orders_live) or requoted_now
        orders_live = True
        tup_now, tdn_now = w.tape[i]
        if can_up and resting_bid_filled(
                resting_up, up_ask,
                [t[0] for t in tup_now
                 if (t[2] if len(t) > 2 else SIDE_SELL) != SIDE_BUY],
                p.tick_size, newly_placed=placed_now):
            filled_up, can_up = True, False
        if can_dn and resting_bid_filled(
                resting_dn, dn_ask,
                [t[0] for t in tdn_now
                 if (t[2] if len(t) > 2 else SIDE_SELL) != SIDE_BUY],
                p.tick_size, newly_placed=placed_now):
            filled_dn, can_dn = True, False

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
            elif (filled_up and not filled_dn and lb is None) or                  (filled_dn and not filled_up and db_bid is None):
                # Book empty at final poll: engine books 0, but the leg still
                # redeems at 1/0. Record true-settlement info for the
                # summarizer's correction (direction from the last observed mid).
                naked_none = True
                resting = resting_up if held_side == "up" else resting_dn
                settle_won, settle_delta = resolve_redemption(
                    {"best_bid": w.up_bb[-1], "best_ask": w.up_ba[-1]},
                    {"best_bid": w.dn_bb[-1], "best_ask": w.dn_ba[-1]},
                    held_side == "up", resting)

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
        "start_delay_sec": start_delay_sec, "is_partial": is_partial,
        "reentry_count": reentry_count,
        "held_side": held_side, "naked_none": naked_none,
        "settle_won": settle_won, "settle_delta": round(settle_delta, 4),
    }


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def bootstrap_ci(pnls: list[float], n_boot: int = 5000, seed: int = 7,
                 clusters: list[str] | None = None):
    """Return (lo, hi, mean) for the bootstrap 95% CI of the mean.

    With `clusters` (e.g. day tags) also returns the day-cluster bootstrap CI
    (resample days with replacement), which respects within-day correlation.
    """
    n = len(pnls)
    if n == 0:
        return (float("nan"),) * 5
    if n == 1:
        return (pnls[0], pnls[0], pnls[0], None, None)
    rng = random.Random(seed)
    mean = statistics.fmean(pnls)
    lo = hi = mean
    if n_boot > 0:
        means = []
        for _ in range(n_boot):
            acc = 0.0
            for _i in range(n):
                acc += pnls[rng.randrange(n)]
            means.append(acc / n)
        means.sort()
        lo, hi = means[int(0.025 * n_boot)], means[min(n_boot - 1, int(0.975 * n_boot) - 1)]
    day_lo = day_hi = None
    if clusters:
        by_day: dict[str, list[float]] = {}
        for v, d in zip(pnls, clusters):
            by_day.setdefault(d, []).append(v)
        days = list(by_day)
        if len(days) >= 2:
            rng2 = random.Random(seed + 1)
            dmeans = []
            day_means = [statistics.fmean(by_day[d]) for d in days]
            day_sizes = [len(by_day[d]) for d in days]
            for _ in range(n_boot):
                acc = 0.0
                tot = 0
                for j in range(len(days)):
                    k = rng2.randrange(len(days))
                    acc += day_means[k] * day_sizes[k]
                    tot += day_sizes[k]
                dmeans.append(acc / tot)
            dmeans.sort()
            day_lo = dmeans[int(0.025 * n_boot)]
            day_hi = dmeans[min(n_boot - 1, int(0.975 * n_boot) - 1)]
    return lo, hi, mean, day_lo, day_hi


def _empty_summary() -> dict:
    """Zero-shaped summary for a selection that matched no windows.

    `summarize` used to return a bare `{"n": 0}` here, but every phase script
    formats its report with `r['pair_rate']`, `r['ci95_lo']` and friends — so
    one series absent from the cache (which `build_cache` rebuilds from
    whatever tick files exist) raised `KeyError` and threw away a sweep that
    had already finished running. Rates are 0.0 rather than None because they
    are "0 of 0" in a report, while the CI bounds stay None: no interval was
    estimated, and printing 0.0 there would read as a measured bound (#182).
    """
    return {
        "n": 0,
        "settle_corrected": 0, "settle_ambiguous": 0,
        "pairs": 0, "pair_rate": 0.0,
        "exits": 0, "exit_rate": 0.0,
        "naked_settle": 0,
        "win_rate": 0.0,
        "total_pnl_usd": 0.0,
        "mean_net_cents": 0.0,
        "roi_pct_per_window": 0.0,
        "ci95_lo": None, "ci95_hi": None,
        "ci95_day_lo": None, "ci95_day_hi": None,
        "max_dd_usd": 0.0,
        "capital_usd": 0.0,
        "profit_factor": 0.0,
        "sharpe_proxy": 0.0,
        "by_series": {}, "by_day": {}, "by_duration": {}, "by_class": {},
        "outcome_decomp": {
            "pairs": {"n": 0, "mean_c": 0.0, "total_usd": 0.0},
            "exits": {"n": 0, "mean_c": 0.0, "total_usd": 0.0},
            "naked": {"n": 0, "mean_c": 0.0, "total_usd": 0.0},
            "no_fill": 0,
        },
    }


def stable_seed(name: str) -> int:
    """Reproducible bootstrap seed for a config name (issue #182).

    `hash()` on a str is salted per process unless `PYTHONHASHSEED` is fixed,
    so seeding the bootstrap with it made every published `ci95_*` bound
    unreproducible by a rerun — and those bounds are the stated selection
    criterion ("95% bootstrap CI lower bound above 0"). `zlib.crc32` is stable
    across processes, platforms and Python versions.
    """
    return zlib.crc32(str(name).encode("utf-8")) & 0xFFFF


def summarize(rows: list[dict], size: int = 5, n_boot: int = 5000, seed: int = 7,
              settle_correct: bool = True) -> dict:
    """Aggregate per-window sim rows into the report metrics.

    `settle_correct=True` replaces the engine's silent 0-PnL for naked legs
    whose final bid is None with true redemption value (1/0 by last-mid
    direction). Run `audit_settlement.py` for the rationale.
    """
    if not rows:
        return _empty_summary()
    size = max(5, int(size))
    n = len(rows)
    amb = 0
    corrected = 0
    net = []
    for r in rows:
        v = r["pnl"] - r["fees"]
        if settle_correct and r.get("naked_none"):
            if r.get("settle_won") is not None:
                v += r.get("settle_delta", 0.0)
                corrected += 1
            else:
                amb += 1
        net.append(v * size)
    wins = sum(1 for v in net if v > 0)
    pairs = sum(1 for r in rows if r["pair"])
    exits = sum(1 for r in rows if r["exit"])
    naked = sum(1 for r in rows if (r["filled_up"] != r["filled_dn"]) and not r["exit"] and not r["pair"])
    total = sum(net)
    cap = sum(r["capital"] for r in rows) * size / 100.0  # USD deployed
    lo, hi, mean, dlo, dhi = bootstrap_ci(net, n_boot=n_boot, seed=seed,
                                          clusters=[r["day"] for r in rows])
    # max drawdown on the cumulative curve
    cum = peak = dd = 0.0
    for v in net:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    out = {
        "n": n,
        "settle_corrected": corrected, "settle_ambiguous": amb,
        "pairs": pairs, "pair_rate": pairs / n,
        "exits": exits, "exit_rate": exits / n,
        "naked_settle": naked,
        "win_rate": wins / n,
        "total_pnl_usd": total / 100.0,
        "mean_net_cents": mean,
        # `total` is cents (see total_pnl_usd above), `cap` is USD. Dividing
        # one by the other without converting reported every ROI 100x too
        # high, which is how a set of negative results read as acceptable
        # ones (issue #182).
        "roi_pct_per_window": ((total / 100.0) / cap * 100.0) if cap > 0 else 0.0,
        "ci95_lo": lo, "ci95_hi": hi,
        "ci95_day_lo": dlo, "ci95_day_hi": dhi,
        "max_dd_usd": dd / 100.0,
        "capital_usd": cap,
    }
    pair_pnls = [net[k] for k, r in enumerate(rows) if r["pair"]]
    exit_pnls = [net[k] for k, r in enumerate(rows) if r["exit"]]
    naked_pnls = [net[k] for k, r in enumerate(rows)
                  if (r["filled_up"] != r["filled_dn"]) and not r["exit"] and not r["pair"]]
    nofill = sum(1 for r in rows if not r["filled_up"] and not r["filled_dn"])
    out["outcome_decomp"] = {
        "pairs": {"n": len(pair_pnls),
                  "mean_c": statistics.fmean(pair_pnls) if pair_pnls else 0.0,
                  "total_usd": sum(pair_pnls) / 100.0},
        "exits": {"n": len(exit_pnls),
                  "mean_c": statistics.fmean(exit_pnls) if exit_pnls else 0.0,
                  "total_usd": sum(exit_pnls) / 100.0},
        "naked": {"n": len(naked_pnls),
                  "mean_c": statistics.fmean(naked_pnls) if naked_pnls else 0.0,
                  "total_usd": sum(naked_pnls) / 100.0},
        "no_fill": nofill,
    }
    gross_g = sum(v for v in net if v > 0)
    gross_l = -sum(v for v in net if v < 0)
    out["profit_factor"] = (gross_g / gross_l) if gross_l > 0 else (999.0 if gross_g > 0 else 0.0)
    if n > 1:
        sd = statistics.stdev(net)
        out["sharpe_proxy"] = (mean / sd) * math.sqrt(n) if sd > 0 else 0.0
    else:
        out["sharpe_proxy"] = 0.0

    def group(keyfn):
        g: dict[str, list[float]] = {}
        cnt: dict[str, dict] = {}
        for r, v in zip(rows, net):
            k = keyfn(r)
            g.setdefault(k, []).append(v)
            c = cnt.setdefault(k, {"n": 0, "pairs": 0, "exits": 0, "wins": 0})
            c["n"] += 1
            c["pairs"] += 1 if r["pair"] else 0
            c["exits"] += 1 if r["exit"] else 0
            c["wins"] += 1 if v > 0 else 0
        res = {}
        for k, vals in g.items():
            m = statistics.fmean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            se = sd / math.sqrt(len(vals)) if len(vals) > 1 else 0.0
            t_lo = m - 1.96 * se  # normal approx for grouped rows
            c = cnt[k]
            res[k] = {
                "n": c["n"], "pairs": c["pairs"], "exits": c["exits"],
                "pair_rate": c["pairs"] / c["n"], "exit_rate": c["exits"] / c["n"],
                "win_rate": c["wins"] / c["n"],
                "mean_net_cents": m, "total_usd": sum(vals) / 100.0,
                "ci95_lo_approx": t_lo,
            }
        return dict(sorted(res.items(), key=lambda kv: -kv[1]["total_usd"]))

    out["by_series"] = group(lambda r: r["series"])
    out["by_day"] = group(lambda r: r["day"])
    out["by_duration"] = group(lambda r: f'{r["duration"]}s')
    out["by_class"] = group(lambda r: r["class_label"])
    return out


# --------------------------------------------------------------------------
# Sweep infra
# --------------------------------------------------------------------------

_CACHE: list[Win] | None = None


def _get_cache() -> list[Win]:
    global _CACHE
    if _CACHE is None:
        _CACHE = load_cache()
    return _CACHE


def default_base_params() -> BacktestParams:
    return BacktestParams(
        offset=0.02, queue_gate=0.0,
        merge_gas_usd=0.0, taker_fee_rate=0.07,
        quote_shares=5,
    )


def filter_windows(cache: list[Win], cfg: dict) -> list[Win]:
    sel = cache
    sf = cfg.get("series_filter")
    if sf:
        sel = [w for w in sel if w.series in set(sf)]
    df = cfg.get("duration_filter")
    if df:
        sel = [w for w in sel if w.duration in set(int(x) for x in df)]
    dayf = cfg.get("day_filter")
    if dayf:
        sel = [w for w in sel if w.day in set(dayf)]
    return sel


def run_config_on(windows: list[Win], params: BacktestParams, size: int = 5,
                  n_boot: int = 5000, seed: int = 7,
                  settle_correct: bool = True) -> dict:
    rows = [fast_simulate(w, params) for w in windows]
    return summarize(rows, size=size, n_boot=n_boot, seed=seed,
                     settle_correct=settle_correct)


def _worker_init():
    global _CACHE
    if _CACHE is None:
        _CACHE = load_cache()


def _worker_task(args):
    cfg, idxs = args
    cache = _get_cache()
    params = BacktestParams(**{**cfg["params_kwargs"]})
    sel = [cache[i] for i in idxs]
    rows = [fast_simulate(w, params) for w in sel]
    return rows


#: Resident cost of the window cache as a multiple of its pickle size.
#: Measured on the 2026-09-14 capture: a 619MB pickle loads to 1.74GB RSS.
CACHE_RSS_FACTOR = 2.8

#: Left for the OS, the parent process, and whatever else the operator is
#: running. Sweeping is not worth swapping the desktop out from under them.
WORKER_RESERVE_BYTES = 2.0 * 1024 ** 3


#: Worker count used when the machine cannot be measured at all. Low on
#: purpose: guessing high costs a thrashing machine and a sweep that may never
#: finish, guessing low costs about 2.5s per config.
UNMEASURABLE_WORKERS = 2


def available_memory_bytes() -> float | None:
    """Free RAM in bytes, or None when it cannot be read.

    `psutil` is not a declared dependency of this repo and is absent from CI,
    so this is a soft probe rather than an import at module scope.
    """
    try:
        import psutil  # noqa: PLC0415 - optional, soft dependency
        return float(psutil.virtual_memory().available)
    except Exception:
        return None


def safe_worker_count(requested: int, reserve_bytes: float = WORKER_RESERVE_BYTES,
                      available_bytes: float | None = None) -> int:
    """Clamp a worker count to what this machine can actually hold.

    Every spawned worker calls `_get_cache()` and holds the *whole* window
    cache: measured 1.74GB resident for a 619MB pickle. The drivers each
    hardcoded 8, which was sized for a much smaller dataset -- against a 24h
    capture that is ~14GB, and the machine thrashes instead of sweeping.
    Workers only speed up a sweep that fits in RAM; one that does not is slower
    than running serially, and may not finish at all.

    `available_bytes` is injectable so the arithmetic can be tested without
    depending on the machine the tests run on. Passing nothing probes the real
    one. This is not hypothetical tidiness: the first version of these tests
    asserted on sizing that CI never reached, because CI has neither `psutil`
    nor a window cache, and they passed locally for a reason that had nothing
    to do with what they claimed to check.
    """
    requested = max(1, int(requested))
    available = (available_memory_bytes() if available_bytes is None
                 else float(available_bytes))
    if available is None:
        return min(requested, UNMEASURABLE_WORKERS)
    try:
        per_worker = CACHE_PATH.stat().st_size * CACHE_RSS_FACTOR
    except OSError:
        return min(requested, UNMEASURABLE_WORKERS)
    if per_worker <= 0:
        return requested
    headroom = available - reserve_bytes
    if not math.isfinite(headroom):
        # `int()` raises on inf/nan rather than clamping, which would turn a
        # sizing question into a crash mid-sweep.
        return 1
    return max(1, min(requested, int(headroom // per_worker)))


class _SerialPool:
    """Stand-in for `multiprocessing.Pool` when only one worker fits.

    `Pool(processes=1)` is not the serial case. The parent already holds the
    window cache and the single spawned worker loads its own copy, so it costs
    twice the memory of running the tasks in-process and buys no parallelism.
    """

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, iterable):
        return [fn(x) for x in iterable]


def sweep_pool(requested: int):
    """A worker pool sized to fit, or an in-process stand-in when it cannot."""
    n = safe_worker_count(requested)
    if n <= 1:
        return _SerialPool()
    from multiprocessing import get_context  # noqa: PLC0415
    return get_context("spawn").Pool(processes=n)


def sweep_configs(configs: list[dict], workers: int = 6, n_boot: int = 3000,
                  chunk_windows: bool = True, size: int = 5) -> list[dict]:
    """Run many configs across the full cache with a process pool.

    Each config dict: {"name": str, "params_kwargs": {...}, optional filters}.
    Returns summaries with config names attached.
    """
    cache = _get_cache()
    pos = {id(w): i for i, w in enumerate(cache)}
    tasks = []
    for cfg in configs:
        # resolve filters in the parent (cheap) to keep workers simple
        sel = cache
        if cfg.get("series_filter"):
            s = set(cfg["series_filter"])
            sel = [w for w in sel if w.series in s]
        if cfg.get("duration_filter"):
            d = set(int(x) for x in cfg["duration_filter"])
            sel = [w for w in sel if w.duration in d]
        if cfg.get("day_filter"):
            d = set(cfg["day_filter"])
            sel = [w for w in sel if w.day in d]
        tasks.append((dict(cfg), [pos[id(w)] for w in sel]))

    results = []
    t0 = time.perf_counter()
    if workers <= 1:
        for cfg, cfg_idx in tasks:
            params = BacktestParams(**cfg["params_kwargs"])
            # `tasks` carries positions, not windows: the parallel branch ships
            # indices to workers that hold their own cache copy. This branch
            # has the cache in hand and has to resolve them. Passing the index
            # list straight through raised `'int' object has no attribute
            # 'duration'`, which nothing hit while `workers` defaulted to 6.
            sel = [cache[i] for i in cfg_idx]
            s = run_config_on(sel, params, size=size, n_boot=n_boot,
                              seed=stable_seed(cfg["name"]))
            s["name"] = cfg["name"]
            s["params_kwargs"] = cfg["params_kwargs"]
            results.append(s)
    else:
        # parallel: shard window lists across workers, reassemble rows in parent
        from multiprocessing import get_context
        ctx = get_context("spawn")
        with ctx.Pool(processes=workers, initializer=_worker_init) as pool:
            shard_tasks = []
            nsh = max(1, workers * 2)
            for cfg, cfg_idx in tasks:
                per = max(1, math.ceil(len(cfg_idx) / nsh))
                for k in range(0, len(cfg_idx), per):
                    shard_tasks.append((cfg, cfg_idx[k:k + per]))
            row_chunks = pool.map(_worker_task2, shard_tasks)
        # regroup rows per config in order
        by_cfg: dict[str, list[dict]] = {cfg["name"]: [] for cfg, _ in tasks}
        for (cfg, _idxs), rows in zip(shard_tasks, row_chunks):
            by_cfg[cfg["name"]].extend(rows)
        for cfg, _sel in tasks:
            rows = by_cfg[cfg["name"]]
            s = summarize(rows, size=size, n_boot=n_boot,
                          seed=stable_seed(cfg["name"]), settle_correct=True)
            s["name"] = cfg["name"]
            s["params_kwargs"] = cfg["params_kwargs"]
            results.append(s)
    print(f"swept {len(configs)} configs in {time.perf_counter()-t0:.0f}s")
    return results


def _worker_task2(args):
    """Shard task: (cfg, global window indexes) -> per-window sim rows."""
    cfg, idxs = args
    cache = _get_cache()
    params = BacktestParams(**cfg["params_kwargs"])
    return [fast_simulate(cache[i], params) for i in idxs]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _parity(file_substr: str = "") -> int:
    """Verify fast_simulate == engine._simulate_window on real windows."""
    files = sorted(p for p in TICKS_DIR.glob("ticks_*.jsonl") if file_substr in p.name)
    if not files:
        print("no matching tick files")
        return 1
    cache = {w.cid: w for w in load_cache()}
    param_sets = [
        default_base_params(),
        replace(default_base_params(), offset=0.01),
        replace(default_base_params(), offset=0.04, queue_gate=50.0),
        replace(default_base_params(), exit_reversal=0.03),
        replace(default_base_params(), queue_gate=25.0),
        replace(default_base_params(), exit_thresh_by_slug={
            "default_5m": 0.05, "default_15m": 0.06,
            "btc-up-or-down-5m": 0.05, "sol-up-or-down-5m": 0.06}),
        replace(default_base_params(), merge_gas_usd=0.05, quote_shares=5),
    ]
    fails = 0
    checked = 0
    for path in files:
        raw_groups = group_by_cid(list(iter_ticks(path)))
        print(f"{path.name}: {len(raw_groups)} windows")
        for pi, params in enumerate(param_sets):
            bad = 0
            for cid, snaps in raw_groups:
                w = cache.get(cid)
                if w is None:
                    continue
                eng = _simulate_window(snaps, params)
                fast = fast_simulate(w, params)
                checked += 1
                same = (
                    eng.pair_captured == fast["pair"]
                    and eng.exit_taken == fast["exit"]
                    and eng.exit_side == fast["exit_side"]
                    and eng.filled_up == fast["filled_up"]
                    and eng.filled_down == fast["filled_dn"]
                    and eng.reentry_count == fast["reentry_count"]
                    and abs(eng.pnl_cents - fast["pnl"]) < 5e-4
                    and abs(eng.fees_cents - fast["fees"]) < 5e-4
                )
                if not same:
                    bad += 1
                    if bad <= 3:
                        print(f"  MISMATCH p{pi} {cid[:12]}: "
                              f"eng pnl={eng.pnl_cents} fees={eng.fees_cents} "
                              f"pair={eng.pair_captured} exit={eng.exit_taken}/{eng.exit_side} "
                              f"re={eng.reentry_count} fu={eng.filled_up} fd={eng.filled_down} | "
                              f"fast pnl={fast['pnl']} fees={fast['fees']} "
                              f"pair={fast['pair']} exit={fast['exit']}/{fast['exit_side']} "
                              f"re={fast['reentry_count']} fu={fast['filled_up']} fd={fast['filled_dn']}")
            if bad:
                print(f"  param set {pi}: {bad} mismatches")
                fails += bad
            else:
                print(f"  param set {pi}: OK")
    print(f"\nparity: {checked} window-checks, {fails} mismatches")
    return 0 if fails == 0 else 2


def _check(cfg_json: str) -> int:
    cfg = json.loads(cfg_json)
    cache = _get_cache()
    sel = filter_windows(cache, cfg)
    params = BacktestParams(**cfg["params_kwargs"])
    s = run_config_on(sel, params, size=cfg.get("size", 5),
                      n_boot=cfg.get("n_boot", 5000))
    print(json.dumps(s, indent=1, default=str)[:6000])
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "cache":
        build_cache(force="--force" in argv)
        return 0
    if cmd == "parity":
        return _parity(argv[1] if len(argv) > 1 else "")
    if cmd == "check":
        return _check(argv[1])
    print(f"unknown command {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
