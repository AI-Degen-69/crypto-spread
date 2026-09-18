"""Dead-zone & naked-leg measurement lab (issues #222 and #223).

Read-only research over the `ev_lab` window cache, in the established pattern
of `verify_205_fill_rate.py` and `audit_settlement.py`: no engine code is
touched, and every fill decision goes through the one shared rule
`book_math.resting_bid_filled` (issue #226 / ADR-0002) with the sell-print
pre-filter (issue #182).

What it measures, per simulated window:

- **#222 (dead-zone unit).** Time-to-first-fill and time-to-pair, measured
  from the moment quotes were placed, split by window length; the verdict
  rule the issue states: roughly constant across durations -> "sec" wins,
  scaling with the window -> "pct" stands.
- **#223 (unpaired leg at expiry).** Legs that reached the dead zone naked:
  bucket by the leg's mid on entering the dead zone (0.05 wide), realised
  settlement rate per bucket vs the bucket's price, best bid vs mid in the
  dead zone, and the realised value of close vs hold including taker fees —
  with full distribution stats, never the mean alone.

Settlement is proxied from the last two-sided mid (`> 0.5` -> up won), the
same convention `audit_settlement.py` uses; ambiguous mids are counted, not
guessed.

Usage:
    python -m research.sweeps.dead_zone_lab [ticks.jsonl ...]

With no arguments, measures every `run/ticks/ticks_*.jsonl` via the lab's
window cache (built once, reused). Emits:

- `research/sweeps/dead_zone_222.json`
- `research/sweeps/naked_leg_223.json`
"""
from __future__ import annotations

import gzip
import json
import math
import sys
import time
from pathlib import Path
from statistics import fmean, median, pstdev, quantiles

REPO_ROOT = Path(__file__).resolve().parents[2]
SWEEPS = REPO_ROOT / "research" / "sweeps"
for p in (str(REPO_ROOT), str(SWEEPS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from strategy import book_math  # noqa: E402
from strategy.book_math import resting_bid_filled  # noqa: E402
from ev_lab import (  # noqa: E402
    SIDE_BUY, Win, _mid_from, _two_sided, _taker_fee,
)

#: Mirrors `ev_lab.default_base_params()` / `sim2` anchoring: quotes rest at
#: `mid - offset` from the first valid two-sided mid inside quote_range.
DEFAULT_OFFSET = 0.02
DEFAULT_TAKER_FEE_RATE = 0.07
QUOTE_RANGE = (0.10, 0.90)

#: #223 bucketing: 0.05-wide dead-zone-mid buckets, lower edge inclusive.
BUCKET_WIDTH = 0.05
#: Buckets below this n are reported but never cited by a verdict (SPEC).
MIN_BUCKET_N = 30
#: Minimum paired windows per duration for the #222 verdict to speak.
MIN_PAIRS_PER_DURATION = 10
#: #223: hold must beat close by more than this many cents per leg to
#: overturn the default — a thinner edge does not pay for the variance
#: (rule 14: "a cent of expected value does not pay for a coin flip").
HOLD_EDGE_CENTS = 1.0
#: #223 settlement-proxy ambiguity band around 0.50.
AMBIGUOUS_MID_BAND = 0.02
#: Tick size, matching BacktestParams.tick_size — used by the shared fill rule.
TICK_SIZE = 0.001


# ---------------------------------------------------------------------------
# shared rule 8 helper (no new dead-zone arithmetic lives here)
# ---------------------------------------------------------------------------

def in_dead_zone(remaining_sec: float, window_length: float,
                 dead_zone_val: float = 0.10, dead_zone_unit: str = "pct") -> bool:
    """`book_math.is_in_dead_zone` re-exported so callers bind one name."""
    return book_math.is_in_dead_zone(remaining_sec, window_length,
                                     dead_zone_val, dead_zone_unit)


# ---------------------------------------------------------------------------
# pure measurement helpers
# ---------------------------------------------------------------------------

def settlement_won(last_two_sided_mid, held_up: bool):
    """Proxy settlement from the last two-sided mid, or None when ambiguous.

    Captured data ends at window end, so the winner is inferred from where
    the market stood: a mid decisively above 0.50 means the up leg won. The
    ambiguity band and missing mids return None — counted, never guessed.
    """
    if last_two_sided_mid is None:
        return None
    m = float(last_two_sided_mid)
    if abs(m - 0.50) < AMBIGUOUS_MID_BAND:
        return None
    decided_up = m > 0.50
    return held_up == decided_up


def close_hold_values(entry: float, bid, won: bool,
                      taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE):
    """Realised net cents per leg for `close` and `hold` (rule 14 arithmetic).

    - close: sell at the dead-zone bid -> (bid - entry) * 100 minus the taker
      fee on that exit. `bid is None` means no executable book -> no close
      (rule 2's "no executable bid = no exit"), so the close value is None.
    - hold: settlement pays 1.00 on a win, 0.00 on a loss -> (payoff - entry)
      * 100, no spread, no fee.
    """
    hold_net = (1.00 if won else 0.00) - entry
    hold_net *= 100.0
    if bid is None:
        return None, hold_net
    fee = _taker_fee(bid, taker_fee_rate) * 100.0
    close_net = (bid - entry) * 100.0 - fee
    return close_net, hold_net


def bucket_of(leg_mid):
    """Lower edge of the 0.05-wide bucket containing `leg_mid` (None-safe)."""
    if leg_mid is None:
        return None
    m = min(1.0, max(0.0, float(leg_mid)))
    return round(math.floor(m / BUCKET_WIDTH) * BUCKET_WIDTH, 2)


def distribution_stats(values) -> dict:
    """Mean, median, std and quartiles — the spread, not just the average."""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return {"n": 0, "mean": None, "median": None, "std": None,
                "p25": None, "p75": None}
    if len(vals) == 1:
        return {"n": 1, "mean": vals[0], "median": vals[0], "std": 0.0,
                "p25": vals[0], "p75": vals[0]}
    q = quantiles(vals, n=4)
    return {"n": len(vals), "mean": round(fmean(vals), 4),
            "median": round(median(vals), 4),
            "std": round(pstdev(vals), 4),
            "p25": round(q[0], 4), "p75": round(q[2], 4)}


# ---------------------------------------------------------------------------
# per-window timeline simulation
# ---------------------------------------------------------------------------

def simulate_window_timeline(w: Win, offset: float = DEFAULT_OFFSET,
                             quote_range=QUOTE_RANGE,
                             taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE) -> dict:
    """Walk one cached window and record the #222/#223 measurement record.

    Anchoring follows `sim2` (issue #225's two-sided anchor): quotes rest at
    `mid - offset` from the first tick whose two-sided mid sits inside
    `quote_range`; the placement tick is marketable (`newly_placed=True`),
    later ticks must be crossed or printed through. Fill detection is the
    shared rule — never reimplemented here.
    """
    duration = float(w.duration or 0)
    start_ts = float(w.start_ts or 0.0)
    rec = {
        "cid": w.cid, "series": w.series, "slug": w.slug,
        "duration": int(w.duration or 0), "day": w.day,
        "entered": False, "excluded": None,
        "placed_elapsed": None,
        "fill_up_sec": None, "fill_dn_sec": None,
        "time_to_first_fill_sec": None, "time_to_pair_sec": None,
        "outcome": "no_fill", "held_side": None, "entry_price": None,
        "dz_elapsed": None, "dz_leg_mid": None, "dz_leg_bid": None,
        "won": None, "close_net_cents": None, "hold_net_cents": None,
    }
    if duration <= 0 or not w.ts:
        rec["excluded"] = "no_data"
        return rec

    resting_up = resting_dn = None
    placed_elapsed = None
    filled_up = filled_dn = False
    entry_up = entry_dn = None
    dz_seen = False
    last_two_sided = None

    n = len(w.ts)
    for i in range(n):
        cur = float(w.ts[i])
        elapsed = max(0.0, cur - start_ts) if start_ts > 0.0 else float(i)
        remaining = max(0.0, duration - elapsed)
        ubb, uba = w.up_bb[i], w.up_ba[i]
        dbb, dba = w.dn_bb[i], w.dn_ba[i]

        om = _two_sided(ubb, uba, dbb, dba)
        if om is not None:
            last_two_sided = om

        in_dz = book_math.is_in_dead_zone(remaining, duration, 0.10, "pct")

        # rule 8: no entry inside the dead zone; a window whose first tick
        # already lands there is not entered at all.
        if resting_up is None:
            if in_dz:
                rec["excluded"] = "starts_in_dead_zone"
                return rec
            if om is None or not (quote_range[0] <= om <= quote_range[1]):
                continue
            resting_up = round(min(0.99, max(0.01, om - offset)), 3)
            resting_dn = round(min(0.99, max(0.01, (1.0 - om) - offset)), 3)
            placed_elapsed = elapsed
            rec["entered"] = True
            rec["placed_elapsed"] = round(elapsed, 3)
            rec["anchor_mid"] = om

        # --- fill detection: the one rule (issue #226), sell-prints only ---
        # Float-compare the elapsed clock with a tolerance: the placement tick
        # is the one that just set `placed_elapsed`, but re-derived floats can
        # differ in the last ulp. Shared tick size constant.
        newly = placed_elapsed is not None and abs(elapsed - placed_elapsed) < 1e-6
        tup_now, tdn_now = w.tape[i] if w.tape[i] else ([], [])
        if not filled_up and resting_bid_filled(
                resting_up, uba,
                [tr[0] for tr in tup_now
                 if (tr[2] if len(tr) > 2 else 0) != SIDE_BUY],
                TICK_SIZE, newly_placed=newly):
            filled_up, entry_up = True, resting_up
            rec["fill_up_sec"] = round(elapsed - placed_elapsed, 3)
        if not filled_dn and resting_bid_filled(
                resting_dn, dba,
                [tr[0] for tr in tdn_now
                 if (tr[2] if len(tr) > 2 else 0) != SIDE_BUY],
                TICK_SIZE, newly_placed=newly):
            filled_dn, entry_dn = True, resting_dn
            rec["fill_dn_sec"] = round(elapsed - placed_elapsed, 3)

        if filled_up and filled_dn:
            rec["outcome"] = "pair"
            rec["time_to_first_fill_sec"] = round(
                min(rec["fill_up_sec"], rec["fill_dn_sec"]), 3)
            rec["time_to_pair_sec"] = round(elapsed - placed_elapsed, 3)
            return rec

        if filled_up != filled_dn and in_dz and not dz_seen:
            # first dead-zone tick of a leg that will expire naked (#223).
            # Capture the held leg's dead-zone book once (valuation point);
            # settlement itself is resolved after the loop from the FINAL
            # two-sided mid — the market can still decide after the dead zone
            # opens, and guessing the winner from the entry tick would score
            # close-vs-hold against a stale outcome.
            dz_seen = True
            held_up = bool(filled_up)
            held_bb, held_ba = (ubb, uba) if held_up else (dbb, dba)
            held_mid = _mid_from(held_bb, held_ba)
            rec["outcome"] = "naked"
            rec["held_side"] = "up" if held_up else "dn"
            rec["entry_price"] = entry_up if held_up else entry_dn
            rec["dz_elapsed"] = round(elapsed, 3)
            rec["dz_leg_mid"] = None if held_mid is None else round(held_mid, 4)
            rec["dz_leg_bid"] = held_bb
    # settlement proxy resolved from the FINAL two-sided mid of the window
    if dz_seen:
        held_up = rec["held_side"] == "up"
        rec["won"] = settlement_won(last_two_sided, held_up)
        close_net, hold_net = close_hold_values(
            rec["entry_price"], rec["dz_leg_bid"],
            bool(rec["won"]) if rec["won"] is not None else False,
            taker_fee_rate)
        rec["close_net_cents"] = None if close_net is None else round(close_net, 4)
        rec["hold_net_cents"] = None if hold_net is None or rec["won"] is None \
            else round((1.00 if rec["won"] else 0.00) - rec["entry_price"], 4) * 100.0
    return rec


# ---------------------------------------------------------------------------
# aggregation + verdicts
# ---------------------------------------------------------------------------

def verdict_dead_zone_222(records: list[dict]) -> dict:
    """Issue #222's decision rule, mechanically applied.

    If median time-to-pair is roughly constant across window lengths, "sec"
    wins; if the untradeable tail scales with the window, "pct" stands.
    Thin samples (fewer than MIN_PAIRS_PER_DURATION pairs per duration) are
    inconclusive by construction.
    """
    by_dur: dict[int, list[float]] = {}
    for r in records:
        if r.get("outcome") == "pair" and r.get("time_to_pair_sec") is not None:
            by_dur.setdefault(int(r["duration"]), []).append(
                float(r["time_to_pair_sec"]))
    medians = {d: round(median(v), 2) for d, v in sorted(by_dur.items())
               if len(v) >= MIN_PAIRS_PER_DURATION}
    out = {"median_ttp_by_duration": medians,
           "median_ttp_5m": medians.get(300),
           "median_ttp_15m": medians.get(900),
           "min_pairs_per_duration": MIN_PAIRS_PER_DURATION,
           "verdict": "inconclusive", "ratio_15m_over_5m": None,
           "rationale": ""}
    if 300 not in medians or 900 not in medians:
        out["rationale"] = ("not enough paired windows in both durations "
                            f"(need >= {MIN_PAIRS_PER_DURATION} each)")
        return out
    ratio = medians[900] / medians[300] if medians[300] > 0 else float("inf")
    out["ratio_15m_over_5m"] = round(ratio, 3) if math.isfinite(ratio) else None
    if ratio < 1.5:
        out["verdict"] = "sec"
        out["rationale"] = ("time-to-pair is roughly constant across window "
                            "lengths; a fixed seconds threshold matches it")
    elif ratio > 2.5:
        out["verdict"] = "pct"
        out["rationale"] = ("time-to-pair scales with the window; the "
                            "untradeable tail scales too, so 10% stands")
    else:
        out["verdict"] = "inconclusive"
        out["rationale"] = ("ratio sits in the ambiguous band (1.5-2.5x); "
                            "neither reading is established")
    return out


def verdict_naked_leg_223(buckets: dict, total_naked: int) -> dict:
    """Issue #223's decision rule, mechanically applied.

    Per bucket: winner = the higher realised net value, cited only at
    n >= MIN_BUCKET_N. Verdict:
    - every cited bucket favours close (or hold's edge <= HOLD_EDGE_CENTS)
      -> "close" (the default stands);
    - every cited bucket favours hold by more than the edge -> "hold";
    - cited buckets disagree by price -> price_dependent with the low/high
      sides, and the switch should take a threshold rather than a boolean.
    """
    per_bucket = {}
    cited = []
    for name, b in sorted(buckets.items()):
        cs, hs = b["close_stats"], b["hold_stats"]
        valuated = cs["n"] > 0 and hs["n"] > 0
        if not valuated:
            winner = None
            edge = None
        else:
            edge = round(hs["mean"] - cs["mean"], 4)
            winner = "hold" if edge > 0 else "close"
        entry = {
            "n": b["n"], "n_valued": b.get("n_valued", cs["n"]),
            "n_close_impossible": b.get("n_close_impossible", 0),
            "settlement_rate": b.get("settlement_rate"),
            "mean_leg_price": b.get("mean_leg_price"),
            "mean_dz_bid": b.get("mean_dz_bid"),
            "mean_bid_minus_mid": b.get("mean_bid_minus_mid"),
            "close_stats": cs, "hold_stats": hs,
            "close_minus_hold": (None if edge is None
                                 else round(-edge, 4)),
            "winner": winner,
            "cited": b["n"] >= MIN_BUCKET_N and valuated,
        }
        per_bucket[name] = entry
        if entry["cited"]:
            cited.append((float(name), entry))

    out = {"total_naked": total_naked, "min_bucket_n": MIN_BUCKET_N,
           "hold_edge_cents": HOLD_EDGE_CENTS,
           "per_bucket": per_bucket,
           "verdict": "inconclusive", "price_dependent": False,
           "low_side": None, "high_side": None, "rationale": ""}
    if not cited:
        out["rationale"] = (f"no bucket reached n >= {MIN_BUCKET_N} with "
                            "valuable legs; nothing to decide on")
        return out

    # Decision rule (issue #223): a hold edge at or below HOLD_EDGE_CENTS
    # counts as "close" — the variance cost outweighs a thin edge (rule 14).
    # Normalize every cited bucket's side through that rule BEFORE the
    # close/hold/threshold selection, so a 0.5c hold-leaning bucket never
    # fabricates a price-dependent threshold verdict on its own.
    sides = {}
    decisive_hold = True
    for nm, e in cited:
        edge = e["hold_stats"]["mean"] - e["close_stats"]["mean"]
        if edge > HOLD_EDGE_CENTS:
            sides[nm] = "hold"
        else:
            sides[nm] = "close"
            decisive_hold = False
    all_hold = all(w == "hold" for w in sides.values())
    if all(w == "close" for w in sides.values()):
        out["verdict"] = "close"
        out["rationale"] = ("every cited bucket realises more by closing — "
                            "the default stands")
    elif all_hold and decisive_hold:
        out["verdict"] = "hold"
        out["rationale"] = ("every cited bucket realises more than the "
                            f"variance edge ({HOLD_EDGE_CENTS}c) by holding")
    else:
        low = min(sides)
        high = max(sides)
        if sides[low] != sides[high]:
            out["price_dependent"] = True
            out["low_side"] = sides[low]
            out["high_side"] = sides[high]
            out["verdict"] = "threshold"
            out["rationale"] = (f"close wins below {low}, hold wins above "
                                f"{high}; the switch should take a threshold")
        else:
            out["verdict"] = "close"
            out["rationale"] = ("hold's edge never clears the variance "
                                "threshold in the cited buckets")
    return out


# ---------------------------------------------------------------------------
# dataset plumbing
# ---------------------------------------------------------------------------

def dataset_header(files: list[str], line_counts: dict) -> dict:
    """Stamp the inputs into every artifact (CONSTRAINTS reproducibility)."""
    return {"files": list(files), "lines": dict(line_counts),
            "params": {"offset": DEFAULT_OFFSET,
                       "quote_range": list(QUOTE_RANGE),
                       "taker_fee_rate": DEFAULT_TAKER_FEE_RATE,
                       "bucket_width": BUCKET_WIDTH,
                       "settlement_proxy": "last two-sided mid > 0.5 -> up won",
                       "dead_zone": "book_math.is_in_dead_zone @ 0.10 pct"}}


def _default_datasets() -> list[Path]:
    ticks = REPO_ROOT / "run" / "ticks"
    return sorted(set(ticks.glob("ticks_*.jsonl")) | set(ticks.glob("ticks_*.jsonl.gz")))


def _get_lab_cache(datasets: list[Path]):
    """Load the ev_lab window cache over exactly the given files (scratch
    cache pattern from verify_205_fill_rate.py — the lab's own cache path is
    restored afterwards and never rebuilt in place).

    `ev_lab.build_cache()` ignores arguments and globs TICKS_DIR itself, so a
    caller-selected subset must be selected *after* the cache loads: when the
    user passes explicit paths, the cache is loaded/built once over the full
    directory and then filtered to the requested files by their stamped `day`
    (cache records carry the source file's day). The header the caller
    receives still describes exactly what was measured.
    """
    import ev_lab
    ticks_dir = datasets[0].parent
    old_ticks, old_cache = ev_lab.TICKS_DIR, ev_lab.CACHE_PATH
    ev_lab.TICKS_DIR = ticks_dir
    scratch = ticks_dir / ".dead_zone_lab_cache"
    scratch.mkdir(exist_ok=True)
    ev_lab.CACHE_PATH = scratch / "window_cache.pkl"
    try:
        ev_lab.build_cache(force=False)
        windows = ev_lab.load_cache()
        wanted_days = {p.stem.replace(".jsonl", "").replace("ticks_", "")
                       for p in datasets}
        return [w for w in windows if getattr(w, "day", None) in wanted_days]
    finally:
        ev_lab.TICKS_DIR, ev_lab.CACHE_PATH = old_ticks, old_cache


def run_measurement(datasets: list[Path]):
    """One pass over the cache -> (#222 record list, #223 bucket dict, counters)."""
    windows = _get_lab_cache(datasets)
    records = []
    buckets: dict[str, dict] = {}
    counters = {"windows": 0, "entered": 0, "pairs": 0, "naked": 0,
                "naked_valued": 0, "naked_no_dz_book": 0,
                "excluded_starts_in_dead_zone": 0, "ambiguous_settlement": 0}
    for w in windows:
        counters["windows"] += 1
        rec = simulate_window_timeline(w)
        records.append(rec)
        if rec["excluded"] == "starts_in_dead_zone":
            counters["excluded_starts_in_dead_zone"] += 1
        if not rec["entered"]:
            continue
        counters["entered"] += 1
        if rec["outcome"] == "pair":
            counters["pairs"] += 1
        elif rec["outcome"] == "naked":
            counters["naked"] += 1
            bname = bucket_of(rec.get("dz_leg_mid"))
            if bname is None:
                counters["naked_no_dz_book"] += 1
                continue
            b = buckets.setdefault(bname, {
                "n": 0, "n_valued": 0, "n_close_impossible": 0,
                "leg_prices": [], "dz_bids": [], "bid_minus_mid": [],
                "close_vals": [], "hold_vals": [], "wins": 0})
            b["n"] += 1
            if rec.get("dz_leg_bid") is not None and rec.get("won") is not None:
                if rec["won"]:
                    b["wins"] += 1
                b["n_valued"] += 1
                counters["naked_valued"] += 1
                b["leg_prices"].append(rec["dz_leg_mid"])
                b["dz_bids"].append(rec["dz_leg_bid"])
                b["bid_minus_mid"].append(rec["dz_leg_bid"] - rec["dz_leg_mid"])
                close_net, hold_net = close_hold_values(
                    rec["entry_price"], rec["dz_leg_bid"], rec["won"],
                    DEFAULT_TAKER_FEE_RATE)
                if close_net is None:
                    b["n_close_impossible"] += 1
                else:
                    b["close_vals"].append(close_net)
                b["hold_vals"].append(hold_net)
            else:
                counters["ambiguous_settlement"] += 1
    out_buckets = {}
    for name, b in buckets.items():
        out_buckets[name] = {
            "n": b["n"], "n_valued": b["n_valued"],
            "n_close_impossible": b["n_close_impossible"],
            "settlement_rate": (round(b["wins"] / b["n_valued"], 4)
                                if b["n_valued"] else None),
            "mean_leg_price": (round(fmean(b["leg_prices"]), 4)
                               if b["leg_prices"] else None),
            "mean_dz_bid": (round(fmean(b["dz_bids"]), 4)
                            if b["dz_bids"] else None),
            "mean_bid_minus_mid": (round(fmean(b["bid_minus_mid"]), 4)
                                   if b["bid_minus_mid"] else None),
            "close_stats": distribution_stats(b["close_vals"]),
            "hold_stats": distribution_stats(b["hold_vals"]),
        }
    return records, out_buckets, counters


def main(argv: list[str]) -> int:
    t0 = time.perf_counter()
    if argv:
        datasets = [Path(a) for a in argv]
    else:
        datasets = _default_datasets()
    if not datasets:
        print("no tick files found under run/ticks — nothing to measure",
              file=sys.stderr)
        return 1
    print(f"datasets: {[str(p) for p in datasets]}")
    line_counts = {}
    for p in datasets:
        n = 0
        op = gzip.open if p.suffix == ".gz" else open
        with op(p, "rt", encoding="utf-8") as f:
            for _ in f:
                n += 1
        line_counts[p] = n
    print(f"total lines: {sum(line_counts.values())}")

    records, out_buckets, counters = run_measurement(datasets)

    # Stamp repository-relative paths: the committed artifacts must not carry
    # a contributor-specific checkout path (reproducibility metadata).
    def rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
        except ValueError:
            return str(p)

    rel_datasets = [rel(p) for p in datasets]
    rel_counts = {rel(p): n for p, n in line_counts.items()}
    header = dataset_header(rel_datasets, rel_counts)
    out_dir = SWEEPS
    v222 = verdict_dead_zone_222(records)
    v223 = verdict_naked_leg_223(out_buckets, counters["naked"])
    art222 = {**header, "counters": counters, "verdict_222": v222}
    art223 = {**header, "counters": counters, "verdict_223": v223,
              "buckets": out_buckets}
    p222 = out_dir / "dead_zone_222.json"
    p223 = out_dir / "naked_leg_223.json"
    p222.write_text(json.dumps(art222, indent=1), encoding="utf-8")
    p223.write_text(json.dumps(art223, indent=1), encoding="utf-8")
    print(f"wrote {p222}")
    print(f"wrote {p223}")
    print(f"#222 verdict: {v222['verdict']} — {v222['rationale']}")
    print(f"#223 verdict: {v223['verdict']} — {v223['rationale']}")
    print(f"elapsed: {time.perf_counter() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
