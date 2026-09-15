"""How good does a config have to look before it means anything?

The phase sweep ran ~200 configurations against one dataset and reported the
best. That number cannot be read as evidence on its own: searching a grid over
the same windows and keeping the winner is a multiple-comparison procedure, and
the winner's confidence interval was computed as though it were the only thing
ever tested.

This builds the null distribution empirically. The profit of the surviving
configs comes almost entirely from naked legs held to expiry, each of which is
one binary outcome. So the null is: *the side a window settles on is unrelated
to anything the strategy did.* Permuting settlement sides across windows
preserves every other structure -- how often each config fills, which windows it
picks, stake sizes, fees, and the heavy overlap between configs that share
windows -- and destroys only the link between strategy and payoff.

Each permutation repeats the whole search and records the *best* config's total.
That answers what the sweep could not: how large a total does the best of the
grid reach by luck alone?

    python research/sweeps/selection_bias.py --perms 2000
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ev_lab import Win, default_base_params, load_cache  # noqa: E402
from sim2 import sim2  # noqa: E402

OUT = Path(__file__).resolve().parent / "selection_bias.json"

HOLD_TO_SETTLEMENT = {k: 0.49 for k in (
    "default_5m", "default_15m", "btc-up-or-down-5m", "sol-up-or-down-5m",
    "btc-up-or-down-15m", "sol-up-or-down-15m")}

SIZE = 5

#: A binary leg pays (1 - resting) when it settles in the money and (-resting)
#: when it does not. The two differ by exactly 1.00 -- 100 cents -- whatever the
#: entry price was. That identity is the whole permutation: flipping an outcome
#: moves that leg's contribution by 100 cents and nothing else.
FLIP_CENTS = 100.0


def build_grid() -> list[dict]:
    """The phase-5 axes -- the grid the reported winner was selected from."""
    out = []
    for off in (0.02, 0.03):
        for band in (0.01, 0.02, 0.03, 0.04, 0.06, None):
            for delay in (0.0, 30.0, 60.0, 120.0):
                for chase in (None, 0.98):
                    for ex_none in (True, False):
                        out.append({
                            "name": (f"off={off}_band={band}_d={delay:.0f}"
                                     f"_{'ch' if chase else 'nc'}"
                                     f"{'_ex=none' if ex_none else ''}"),
                            "offset": off, "band": band, "delay": delay,
                            "chase": chase, "ex_none": ex_none})
    return out


def run_config(cache: list[Win], cfg: dict) -> list[dict]:
    p = replace(default_base_params(), offset=cfg["offset"], fill_model="tape")
    if cfg["ex_none"]:
        p = replace(p, exit_thresh_by_slug=dict(HOLD_TO_SETTLEMENT))
    return [sim2(w, p, chase_cap=cfg["chase"],
                 entry_delay_sec=cfg["delay"], entry_band=cfg["band"])
            for w in cache]


def realized_net(r: dict) -> float:
    """Net cents for one window, with the settlement correction applied.

    Mirrors `summarize(settle_correct=True)`: a naked leg whose final bid had
    vanished is worth its redemption value, not the engine's silent zero.
    """
    v = r["pnl"] - r["fees"]
    if r.get("naked_none") and r.get("settle_won") is not None:
        v += r["settle_delta"]
    return v


def leg_outcome(r: dict, cache_by_cid: dict):
    """`(held_leg_won, up_side_won)` for a naked leg carried to expiry.

    `(None, None)` when the window is not one: pairs and stopped exits are
    determined by the price path, not by settlement, and are not what the
    permutation touches.

    Both values are needed, and conflating them is a real bug. "Our leg won" is
    a property of the *position*, not of the window -- two configs can hold
    opposite legs of the same market, so keying the permutation on it made one
    window carry two contradictory settlements. Measured: 5 such conflicts in
    115 windows over just 40 configs. The window-level fact is which SIDE
    settled in the money; whether that paid us depends on the leg we held.
    """
    if not (r["filled_up"] or r["filled_dn"]):
        return None, None
    if r["pair"] or r["exit"] or not r["held_side"]:
        return None, None
    if r.get("settle_won") is not None:
        won = bool(r["settle_won"])
    else:
        w = cache_by_cid.get(r["cid"])
        if w is None:
            return None, None
        bb = w.up_bb[-1] if r["held_side"] == "up" else w.dn_bb[-1]
        ba = w.up_ba[-1] if r["held_side"] == "up" else w.dn_ba[-1]
        mark = bb if bb is not None else ba
        if mark is None:
            return None, None
        won = bool(mark > 0.5)
    up_won = won if r["held_side"] == "up" else (not won)
    return won, up_won


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--perms", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260914)
    a = ap.parse_args(argv)

    cache = load_cache()
    by_cid = {w.cid: w for w in cache}
    grid = build_grid()
    print(f"windows {len(cache)}  configs {len(grid)}  perms {a.perms}")

    # ---- one real pass, decomposed into fixed + flippable ------------------
    t0 = time.perf_counter()
    per_cfg: dict[str, dict] = {}
    real_up: dict[str, bool] = {}     # cid -> did the UP side settle in the money
    conflicts = 0
    for i, cfg in enumerate(grid, 1):
        fixed = 0.0
        # cid -> (net cents AS OBSERVED, held side, did our leg win)
        legs: dict[str, tuple[float, str, bool]] = {}
        for r in run_config(cache, cfg):
            won, up_won = leg_outcome(r, by_cid)
            net = realized_net(r)
            if won is None:
                fixed += net
            else:
                legs[r["cid"]] = (net, r["held_side"], won)
                if real_up.setdefault(r["cid"], up_won) != up_won:
                    conflicts += 1
        per_cfg[cfg["name"]] = {"fixed": fixed, "legs": legs}
        if i % 20 == 0:
            print(f"  {i}/{len(grid)} configs ({time.perf_counter() - t0:.0f}s)")
    if conflicts:
        raise SystemExit(
            f"{conflicts} windows reported two different settlement sides; the "
            "null would be built on noise. Investigate before trusting this.")

    def total(name: str, up_outcomes: dict) -> float:
        d = per_cfg[name]
        t = d["fixed"]
        for cid, (net, side, won_real) in d["legs"].items():
            won = up_outcomes[cid] if side == "up" else (not up_outcomes[cid])
            if won == won_real:
                t += net
            else:
                # A binary leg pays (1 - resting) or (-resting); the two differ
                # by exactly 100 cents whatever the entry price was.
                t += net - FLIP_CENTS if won_real else net + FLIP_CENTS
        return t * SIZE / 100.0

    observed = {n: total(n, real_up) for n in per_cfg}
    best_name = max(observed, key=observed.get)
    best_obs = observed[best_name]
    n_legs = len(per_cfg[best_name]["legs"])
    ranked = sorted(observed.values())
    print(f"\nobserved best   {best_name} = {best_obs:+.2f}$  ({n_legs} binary legs)")
    print(f"observed median config             {ranked[len(ranked) // 2]:+.2f}$")
    print(f"observed configs above zero        "
          f"{sum(1 for v in ranked if v > 0)}/{len(ranked)}")

    # ---- permutation null --------------------------------------------------
    cids = sorted(real_up)
    print(f"\npermuting {len(cids)} settled windows x {a.perms}")
    rng = random.Random(a.seed)
    vals = [real_up[c] for c in cids]
    null_best, null_named = [], []
    for k in range(a.perms):
        rng.shuffle(vals)
        perm = dict(zip(cids, vals))
        tots = [total(n, perm) for n in per_cfg]
        null_best.append(max(tots))
        null_named.append(total(best_name, perm))
        if (k + 1) % 250 == 0:
            print(f"  {k + 1}/{a.perms} ({time.perf_counter() - t0:.0f}s)")

    null_best.sort()
    p_family = sum(1 for v in null_best if v >= best_obs) / len(null_best)
    p_single = sum(1 for v in null_named if v >= best_obs) / len(null_named)

    def q(p):
        return null_best[min(len(null_best) - 1, int(p * len(null_best)))]

    bar = "=" * 70
    print("\n" + bar)
    print(f"observed best of {len(grid)} configs        {best_obs:+8.2f}$   {best_name}")
    print(f"null  best-of-{len(grid)}  median           {q(0.50):+8.2f}$")
    print(f"null  best-of-{len(grid)}  95th pct         {q(0.95):+8.2f}$")
    print(f"null  best-of-{len(grid)}  99th pct         {q(0.99):+8.2f}$")
    print()
    print(f"p family-wise (what it means)      {p_family:.4f}")
    print(f"p naive single-config              {p_single:.4f}")
    print(bar)
    print("\nThe naive p is what the sweep implicitly reported. The family-wise p")
    print("is what it means, given the winner was CHOSEN as the best of the grid.")
    print("A config has to clear the 95th percentile of the null before the")
    print("search itself stops being a sufficient explanation.")

    OUT.write_text(json.dumps({
        "windows": len(cache), "configs": len(grid), "perms": a.perms,
        "settled_windows": len(cids), "best_name": best_name,
        "best_binary_legs": n_legs, "best_observed_usd": best_obs,
        "null_median": q(0.50), "null_p95": q(0.95), "null_p99": q(0.99),
        "p_family_wise": p_family, "p_naive_single": p_single,
        "observed_totals": observed,
    }, indent=1), encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
