"""Phase 5: entry band (undecided-market regime filter) x delay x chase.

Grid over entry_band in {0.01..0.06, None}, delay in {0, 30, 60, 120},
chase in {0.98, none}, on tape fills at offset 0.02/0.03, exit 0.08.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from multiprocessing import get_context

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams  # noqa: E402
from ev_lab import _get_cache, summarize, default_base_params, stable_seed  # noqa: E402
from sim2 import sim2  # noqa: E402

OUT = Path(__file__).resolve().parent / "phase5_band.json"

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
    ex_none = {k: 0.49 for k in ("default_5m", "default_15m", "btc-up-or-down-5m",
                                 "sol-up-or-down-5m", "btc-up-or-down-15m",
                                 "sol-up-or-down-15m")}
    for off in (0.02, 0.03):
        for band in (None, 0.06, 0.04, 0.03, 0.02, 0.01):
            for delay in (0.0, 30.0, 60.0, 120.0):
                for chase, tag in ((0.98, "ch"), (0.0, "nc")):
                    if band is None and delay == 0.0 and tag == "nc" and off == 0.02:
                        continue  # duplicate of BASE
                    name = (f"off={off:.2f}_band={'none' if band is None else f'{band:.2f}'}"
                            f"_d={delay:.0f}_{tag}")
                    tasks.append({
                        "name": name,
                        "params": replace(BASE, offset=off, fill_model="tape",
                                          exit_thresh_by_slug=exit_dict(0.08)),
                        "chase_cap": chase,
                        "entry_delay_sec": delay,
                        "entry_band": band,
                    })
    # band x exit=none (band + delay replaces stop-loss discipline?)
    for band in (0.06, 0.04, 0.03):
        for delay in (0.0, 60.0):
            name = f"off=0.03_band={band:.2f}_d={delay:.0f}_nc_ex=none"
            tasks.append({
                "name": name,
                "params": replace(BASE, offset=0.03, fill_model="tape",
                                  exit_thresh_by_slug=ex_none),
                "chase_cap": 0.0,
                "entry_delay_sec": delay,
                "entry_band": band,
            })
    return tasks


def _worker(args):
    idxs, params_dict, chase_cap, delay, band = args
    # `_get_cache` memoises in a process-level global; `load_cache`
    # re-unpickles ~336MB per call, and pool.map dispatches one task
    # per shard per config (issue #182).
    from ev_lab import _get_cache
    cache = _get_cache()
    params = BacktestParams(**params_dict)
    return [sim2(cache[i], params, chase_cap=chase_cap or None,
                 entry_delay_sec=delay or 0.0,
                 entry_band=band) for i in idxs]


def main() -> int:
    cache = _get_cache()
    pos_all = list(range(len(cache)))
    tasks = build_tasks()
    print(f"windows: {len(cache)}  tasks: {len(tasks)}")

    ctx = get_context("spawn")
    results = []
    with ctx.Pool(processes=8) as pool:
        for t in tasks:
            pd = {k: v for k, v in asdict(t["params"]).items()}
            shards = [pos_all[k:k + 400] for k in range(0, len(pos_all), 400)]
            res = pool.map(_worker, [(sh, pd, t["chase_cap"],
                                      t.get("entry_delay_sec", 0.0),
                                      t.get("entry_band")) for sh in shards])
            rows = [r for chunk in res for r in chunk]
            s = summarize(rows, size=5, n_boot=2000, seed=stable_seed(t["name"]),
                          settle_correct=True)
            s["name"] = t["name"]
            s["entry_band"] = t.get("entry_band")
            s["entry_delay_sec"] = t.get("entry_delay_sec", 0.0)
            results.append(s)

    # `0.0 or -9e9` is -9e9, so a break-even config sorted below every
    # loss. Test for None explicitly (issue #182).
    results.sort(key=lambda r: -(r["total_pnl_usd"]
                                 if r.get("total_pnl_usd") is not None else -9e9))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, default=str)

    print(f"\n{'config':<40}{'pairs':>7}{'exits':>7}{'win%':>6}"
          f"{'tot$':>9}{'mean c':>8}{'ci_lo':>8}{'day_lo':>8}{'PF':>6}")
    for r in results[:30]:
        d = r.get("ci95_day_lo")
        print(f"{r['name']:<40}{r['pair_rate']*100:>6.1f}%{r['exit_rate']*100:>6.1f}%"
              f"{r['win_rate']*100:>5.1f}%{r['total_pnl_usd']:>+9.2f}{r['mean_net_cents']:>+8.2f}"
              f"{r['ci95_lo']:>+8.2f}{(d if d is not None else float('nan')):>+8.2f}"
              f"{r['profit_factor']:>6.2f}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
