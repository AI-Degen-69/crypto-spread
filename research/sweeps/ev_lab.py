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

from backtest.engine import (  # noqa: E402
    BacktestParams,
    _mid,
    _simulate_window,
    group_by_cid,
    iter_ticks,
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
    else 0` made every print look like a sell, and under `fill_model="tapeq"`
    a buy that lifted the ask could fill our resting bid. `phase6_tapeq_top.py`
    runs exactly that model.

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
#: `max_start_delay_sec` predates them and has the same problem: the engine
#: drops late-start windows at `engine.py:1083`, neither simulator here does.
ENGINE_ONLY_KNOBS = ("stop_loss_enabled", "exit_thresh_naked",
                     "naked_leg_timeout_pct", "enable_leg_chase",
                     "max_start_delay_sec")

#: Fields `engine._simulate_window` honours that `fast_simulate` does not.
UNSUPPORTED_KNOBS = ("entry_delay_sec", "entry_band") + ENGINE_ONLY_KNOBS


def _non_default_knobs(p: BacktestParams, names: tuple[str, ...]) -> list[str]:
    """Which of `names` `p` sets away from its dataclass default.

    Compared against the declared default rather than tested for truthiness,
    because `stop_loss_enabled` defaults to `True`: a truthiness test would
    reject every ordinary config and wave through `stop_loss_enabled=False`,
    the one value that changes what is being simulated.
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
            "comparable. Use research/sweeps/sim2.py for entry_delay_sec and "
            "entry_band; nothing in research/ implements "
            f"{', '.join(ENGINE_ONLY_KNOBS)}."
        )


def reject_knobs_sim2_ignores(p: BacktestParams) -> None:
    """Raise when `p` sets a knob `sim2` would silently ignore.

    `sim2` takes `entry_delay_sec` and `entry_band` as its own arguments and
    never reads the `BacktestParams` fields of the same name, so setting them
    on the params is as silent as not implementing them at all. The four
    `ENGINE_ONLY_KNOBS` it does not implement in any form.
    """
    ignored = _non_default_knobs(p, UNSUPPORTED_KNOBS)
    if ignored:
        raise ValueError(
            f"sim2 ignores {', '.join(ignored)} on BacktestParams; pass "
            "entry_delay_sec / entry_band as sim2() arguments, and note that "
            f"{', '.join(ENGINE_ONLY_KNOBS)} are not implemented in research/ "
            "at all — express hold-to-settlement through exit_thresh_by_slug."
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
    late_start = bool(
        p.max_start_elapsed_pct and p.max_start_elapsed_pct > 0
        and duration > 0 and raw_delay >= p.max_start_elapsed_pct * duration
    )

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
    entry_cancelled = late_start
    # Queue-adjusted tape model state (fill_model="tapeq"): snapshot the queue
    # ahead when quoting starts, burn it down with tape trades at our price,
    # and treat trades strictly through our price as guaranteed fills.
    q_up = q_dn = None
    q_rest_up = q_rest_dn = None
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

    timeout_on = p.entry_timeout_pct is not None and p.entry_timeout_pct > 0 and duration > 0
    if timeout_on and raw_delay >= p.entry_timeout_pct * duration:
        entry_cancelled = True

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

        if max_dn >= exit_thr and (0.50 - mid) < p.exit_reversal:
            reversal_dn = True
        if max_up >= exit_thr and (mid - 0.50) < p.exit_reversal:
            reversal_up = True

        if not gate_evaluated and not late_start:
            om = _two_sided(w.up_bb[i], w.up_ba[i], w.dn_bb[i], w.dn_ba[i])
            if om is not None:
                gate_evaluated = True
                if abs(om - 0.50) >= exit_thr:
                    entry_cancelled = True
                    adverse_skipped = True

        if (adverse_skipped and not filled_up and not filled_dn
                and reentry_count < p.max_reentries_per_window):
            cutoff = (p.entry_timeout_pct * duration
                      if (p.entry_timeout_pct is not None
                          and 0.0 < p.entry_timeout_pct < 1.0 and duration > 0)
                      else None)
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
                    and (cutoff is None or elapsed < cutoff)
                    and drift <= min(p.reentry_drift_band, exit_thr)
                    and drift < exit_thr):
                entry_cancelled = False
                adverse_skipped = False
                reentry_count += 1
                r_mid = w.s_mid[i] if w.s_mid[i] is not None else rm
                if not filled_up:
                    resting_up = round(min(0.99, max(0.01, r_mid - p.offset)), 3)
                if not filled_dn:
                    resting_dn = round(min(0.99, max(0.01, (1.0 - r_mid) - p.offset)), 3)

        if p.queue_gate is not None and p.queue_gate > 0:
            # Separate locals from the tapeq state below (issue #182). These
            # used to write `q_up`/`q_dn`, which the tapeq block treats as
            # "queue not yet latched" sentinels: once the gate had filled them,
            # `q_rest_up`/`q_rest_dn` were never set, and every tapeq fill path
            # requires them — so `fill_model="tapeq"` could not fill at all
            # whenever `queue_gate > 0`.
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

        can_up = (not filled_up) and (not entry_cancelled or filled_dn)
        can_dn = (not filled_dn) and (not entry_cancelled or filled_up)
        fm = p.fill_model
        # tapeq: (re)initialize the queue snapshot on the first tick where the
        # leg is quotable (post-gates). Research-only fill model (no engine
        # equivalent): queue ahead at rest, burned down by printed size, and
        # any trade strictly through our price fills us regardless of queue.
        if fm == "tapeq":
            if can_up and q_up is None and not entry_cancelled:
                q_rest_up = resting_up
                q_up = _queue_ahead(w.up_bids[i], resting_up)
            if can_dn and q_dn is None and not entry_cancelled:
                q_rest_dn = resting_dn
                q_dn = _queue_ahead(w.dn_bids[i], resting_dn)
            if can_up and q_rest_up is not None and up_ask is not None                     and up_ask <= (q_rest_up - p.tick_size + 1e-6):
                filled_up, can_up = True, False
            if can_dn and q_rest_dn is not None and dn_ask is not None                     and dn_ask <= (q_rest_dn - p.tick_size + 1e-6):
                filled_dn, can_dn = True, False
        # Tape entries are (price, size, side) triples; side is 1 for a buy
        # that lifted the ask, which cannot fill a resting bid (issue #182).
        # Tolerate 2-tuples so a cache written before CACHE_VERSION 2 fails
        # at the version guard in load_cache rather than here.
        tup_now, tdn_now = w.tape[i]
        if can_up and fm in ("tape", "both", "cross", "tapeq"):
            for _tr in tup_now:
                tpx, tsz = _tr[0], _tr[1]
                tside = _tr[2] if len(_tr) > 2 else SIDE_SELL
                if fm in ("tape", "both"):
                    if abs(tpx - resting_up) <= (p.tick_size + 1e-6):
                        filled_up, can_up = True, False
                        break
                elif fm == "cross":
                    if tpx <= (resting_up - p.tick_size + 1e-6):
                        filled_up, can_up = True, False
                        break
                else:  # tapeq
                    if tside == SIDE_BUY:
                        continue  # buy lifted the ask; cannot fill our bid
                    if q_rest_up is not None and tpx <= (q_rest_up - p.tick_size + 1e-6):
                        filled_up, can_up = True, False
                        break
                    if q_rest_up is not None and abs(tpx - q_rest_up) <= (p.tick_size + 1e-6):
                        if q_up is None or q_up - tsz <= 0.0:
                            filled_up, can_up = True, False
                            break
                        q_up -= tsz
        if can_dn and fm in ("tape", "both", "cross", "tapeq"):
            for _tr in tdn_now:
                tpx, tsz = _tr[0], _tr[1]
                tside = _tr[2] if len(_tr) > 2 else SIDE_SELL
                if fm in ("tape", "both"):
                    if abs(tpx - resting_dn) <= (p.tick_size + 1e-6):
                        filled_dn, can_dn = True, False
                        break
                elif fm == "cross":
                    if tpx <= (resting_dn - p.tick_size + 1e-6):
                        filled_dn, can_dn = True, False
                        break
                else:  # tapeq
                    if tside == SIDE_BUY:
                        continue  # buy lifted the ask; cannot fill our bid
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
        elif fm == "tapeq":
            pass  # handled above
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
            elif (filled_up and not filled_dn and lb is None) or                  (filled_dn and not filled_up and db_bid is None):
                # Book empty at final poll: engine books 0, but the leg still
                # redeems at 1/0. Record true-settlement info for the
                # summarizer's correction (direction from the last observed mid).
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
        offset=0.02, queue_gate=0.0, pair_cost_gate=1.05,
        fill_model="tape", merge_gas_usd=0.0, taker_fee_rate=0.07,
        quote_shares=5, entry_timeout_pct=0.0,
        max_start_elapsed_pct=0.0,
        reentry_drift_band=0.0, min_requote_remaining_sec=0.0,
        reentry_min_remaining_pct=0.0, max_reentries_per_window=0,
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
        for cfg, sel in tasks:
            params = BacktestParams(**cfg["params_kwargs"])
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
        replace(default_base_params(), entry_timeout_pct=0.10),
        replace(default_base_params(), entry_timeout_pct=0.10, max_start_elapsed_pct=0.10),
        replace(default_base_params(), fill_model="book"),
        replace(default_base_params(), fill_model="cross"),
        replace(default_base_params(), pair_cost_gate=1.01, queue_gate=25.0),
        replace(default_base_params(), exit_thresh_by_slug={
            "default_5m": 0.05, "default_15m": 0.06,
            "btc-up-or-down-5m": 0.05, "sol-up-or-down-5m": 0.06}),
        replace(default_base_params(), reentry_drift_band=0.015,
                min_requote_remaining_sec=60.0, reentry_min_remaining_pct=0.30,
                max_reentries_per_window=1),
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
