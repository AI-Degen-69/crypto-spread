"""Phase 3: mechanical edge variants with sim2 (tapeq + leg-chase pairs rule).

Configs sweep chase_cap x offset x exit threshold on queue-aware fills, plus
chase-on-tape (conservative) comparisons. All results settle-corrected.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams  # noqa: E402
from ev_lab import _get_cache, summarize, default_base_params, stable_seed, sweep_pool  # noqa: E402
from sim2 import sim2  # noqa: E402

OUT = Path(__file__).resolve().parent / "phase3_mech.json"

BASE = default_base_params()


def exit_dict(e5: float) -> dict:
    return {
        "default_5m": e5, "default_15m": round(e5 + 0.01, 2),
        "btc-up-or-down-5m": max(0.03, round(e5 - 0.03, 2)),
        "sol-up-or-down-5m": max(0.04, round(e5 - 0.01, 2)),
        "btc-up-or-down-15m": round(e5 + 0.01, 2),
        "sol-up-or-down-15m": round(e5 + 0.01, 2),
    }


def build_tasks() -> list[dict]:
    tasks = []
    # chase cap ladder on tapeq at offset 0.02, exit 0.08
    for cap in (0.98, 0.96, 0.94, 0.90):
        for off in (0.02, 0.03):
            tasks.append({
                "name": f"tq_chase={cap:.2f}_off={off:.3f}_ex=0.08",
                "params": replace(BASE, offset=off, fill_model="tapeq",
                                  exit_thresh_by_slug=exit_dict(0.08)),
                "chase_cap": cap,
            })
    # chase with exit=none (chase IS the exit: pairs or settle)
    for cap in (0.98, 0.96, 0.94, 0.90):
        tasks.append({
            "name": f"tq_chase={cap:.2f}_off=0.020_ex=none",
            "params": replace(BASE, offset=0.02, fill_model="tapeq",
                              exit_thresh_by_slug={k: 0.49 for k in (
                                  "default_5m", "default_15m", "btc-up-or-down-5m",
                                  "sol-up-or-down-5m", "btc-up-or-down-15m",
                                  "sol-up-or-down-15m")}),
            "chase_cap": cap,
        })
    # chase on plain tape fills (most conservative) at 0.98
    for off in (0.02, 0.03):
        tasks.append({
            "name": f"tape_chase=0.98_off={off:.3f}_ex=none",
            "params": replace(BASE, offset=off, fill_model="tape",
                              exit_thresh_by_slug={k: 0.49 for k in (
                                  "default_5m", "default_15m", "btc-up-or-down-5m",
                                  "sol-up-or-down-5m", "btc-up-or-down-15m",
                                  "sol-up-or-down-15m")}),
            "chase_cap": 0.98,
        })
        tasks.append({
            "name": f"tape_chase=0.98_off={off:.3f}_ex=0.08",
            "params": replace(BASE, offset=off, fill_model="tape",
                              exit_thresh_by_slug=exit_dict(0.08)),
            "chase_cap": 0.98,
        })
    # tapeq no-chase baselines for the delta
    tasks.append({"name": "tq_nochase_off=0.020_ex=0.08",
                  "params": replace(BASE, offset=0.02, fill_model="tapeq",
                                    exit_thresh_by_slug=exit_dict(0.08)),
                  "chase_cap": 0.0})
    tasks.append({"name": "tape_nochase_off=0.020_ex=0.08",
                  "params": replace(BASE, offset=0.02, fill_model="tape",
                                    exit_thresh_by_slug=exit_dict(0.08)),
                  "chase_cap": 0.0})
    return tasks


def _worker(args):
    idxs, params_dict, chase_cap = args
    # `_get_cache` memoises in a process-level global; `load_cache`
    # re-unpickles ~336MB per call, and pool.map dispatches one task
    # per shard per config (issue #182).
    from ev_lab import _get_cache
    cache = _get_cache()
    params = BacktestParams(**params_dict)
    return [sim2(cache[i], params, chase_cap=chase_cap or None) for i in idxs]


def main() -> int:
    cache = _get_cache()
    pos = list(range(len(cache)))
    tasks = build_tasks()
    print(f"windows: {len(cache)}  tasks: {len(tasks)}")

    all_rows: dict[str, list[dict]] = {}
    with sweep_pool(8) as pool:
        for t in tasks:
            pd = {k: v for k, v in asdict(t["params"]).items()}
            shards = [pos[k:k + 400] for k in range(0, len(pos), 400)]
            res = pool.map(_worker, [(sh, pd, t["chase_cap"]) for sh in shards])
            rows = [r for chunk in res for r in chunk]
            all_rows[t["name"]] = rows
            print(f"  simmed {t['name']}")

    results = []
    for t in tasks:
        rows = all_rows[t["name"]]
        s = summarize(rows, size=5, n_boot=2000, seed=stable_seed(t["name"]),
                      settle_correct=True)
        s["name"] = t["name"]
        s["chase_cap"] = t["chase_cap"]
        results.append(s)
    # `0.0 or -9e9` is -9e9, so a break-even config sorted below every
    # loss. Test for None explicitly (issue #182).
    results.sort(key=lambda r: -(r["total_pnl_usd"]
                                 if r.get("total_pnl_usd") is not None else -9e9))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, default=str)

    print(f"\n{'config':<34}{'pairs':>7}{'chased':>8}{'exits':>7}{'win%':>6}"
          f"{'tot$':>9}{'mean c':>8}{'ci_lo':>8}{'day_lo':>8}{'PF':>6}{'DD$':>8}")
    for r in results:
        chased = sum(1 for x in all_rows[r["name"]] if x.get("chased"))
        d = r.get("ci95_day_lo")
        print(f"{r['name']:<34}{r['pair_rate']*100:>6.1f}%{chased:>8}"
              f"{r['exit_rate']*100:>6.1f}%{r['win_rate']*100:>5.1f}%"
              f"{r['total_pnl_usd']:>+9.2f}{r['mean_net_cents']:>+8.2f}"
              f"{r['ci95_lo']:>+8.2f}{(d if d is not None else float('nan')):>+8.2f}"
              f"{r['profit_factor']:>6.2f}{r['max_dd_usd']:>8.2f}")

    print("\noutcome decomposition:")
    for r in results:
        od = r.get("outcome_decomp", {})
        segs = []
        for k in ("pairs", "exits", "naked"):
            s = od.get(k, {})
            segs.append(f"{k}: {s.get('n', 0)}/{s.get('mean_c', 0):+.1f}c/{s.get('total_usd', 0):+.1f}$")
        print(f"  {r['name']:<34} " + "  ".join(segs))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
