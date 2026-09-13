"""Phase 4: universe selection (per-series, per-duration) x entry delay.

Runs the phase-3 best mechanical profile (tape fills, chase 0.98, offset 0.03,
exit 0.08) and variants across every series/timeframe combination, plus an
entry-delay ladder (0/30/60/120s on 5m, 0/60/120/240s on 15m).
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
from ev_lab import _get_cache, summarize, default_base_params  # noqa: E402
from sim2 import sim2  # noqa: E402

OUT = Path(__file__).resolve().parent / "phase4_universe.json"

BASE = default_base_params()
SERIES_5M = [f"{a}-up-or-down-5m" for a in ("btc", "eth", "bnb", "sol", "xrp")]
SERIES_15M = [f"{a}-up-or-down-15m" for a in ("btc", "eth", "bnb", "sol", "xrp")]


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
    best = dict(fill_model="tape", offset=0.03,
                exit_thresh_by_slug=exit_dict(0.08))
    CHASE = 0.98

    # A. per-series: each series alone, best profile
    for s in SERIES_5M + SERIES_15M:
        tasks.append({
            "name": f"series={s}",
            "params": replace(BASE, **best),
            "chase_cap": 0.98,
            "series_filter": [s],
        })

    # B. duration-only universes
    tasks.append({"name": "universe=5m_all",
                  "params": replace(BASE, **best), "chase_cap": CHASE,
                  "series_filter": SERIES_5M})
    tasks.append({"name": "universe=15m_all",
                  "params": replace(BASE, **best), "chase_cap": CHASE,
                  "series_filter": SERIES_15M})
    # C. ranked combos from phase-3 by-series signals
    for name, sel in (
        ("universe=xrp15+bnb15", SERIES_15M[4:5] + SERIES_15M[2:3]),
        ("universe=xrp15+bnb15+eth15", SERIES_15M[4:5] + SERIES_15M[2:3] + SERIES_15M[1:2]),
        ("universe=all_except_btc15", SERIES_5M + [s for s in SERIES_15M if "btc" not in s]),
        ("universe=xrp15+eth5", [SERIES_15M[4], SERIES_5M[1]]),
    ):
        tasks.append({"name": name, "params": replace(BASE, **best),
                      "chase_cap": 0.98, "series_filter": sel})

    # D. entry-delay ladder (all series) on tape/chase profile
    for delay in (30.0, 60.0, 120.0):
        tasks.append({"name": f"delay={delay:.0f}s_all",
                      "params": replace(BASE, **best), "chase_cap": CHASE,
                      "entry_delay_sec": delay})
    # E. entry-delay x the 15m-only universe (delays are a bigger fraction there)
    for delay in (60.0, 120.0, 240.0):
        tasks.append({"name": f"delay={delay:.0f}s_15m",
                      "params": replace(BASE, **best), "chase_cap": CHASE,
                      "entry_delay_sec": delay, "series_filter": SERIES_15M})
    # F. delay ladder without chase, exit=none (pure set-and-forget)
    for delay in (60.0, 120.0):
        tasks.append({"name": f"delay={delay:.0f}s_nochase_ex=none",
                      "params": replace(BASE, offset=0.03, fill_model="tape",
                                        exit_thresh_by_slug={k: 0.49 for k in (
                                            "default_5m", "default_15m",
                                            "btc-up-or-down-5m", "sol-up-or-down-5m",
                                            "btc-up-or-down-15m", "sol-up-or-down-15m")}),
                      "chase_cap": 0.0, "entry_delay_sec": delay})
    return tasks


def _worker(args):
    idxs, params_dict, chase_cap, delay = args
    # `_get_cache` memoises in a process-level global; `load_cache`
    # re-unpickles ~336MB per call, and pool.map dispatches one task
    # per shard per config (issue #182).
    from ev_lab import _get_cache
    cache = _get_cache()
    params = BacktestParams(**params_dict)
    return [sim2(cache[i], params, chase_cap=chase_cap or None,
                 entry_delay_sec=delay or 0.0) for i in idxs]


def main() -> int:
    cache = _get_cache()
    pos_by_series: dict[str, list[int]] = {}
    for i, w in enumerate(cache):
        pos_by_series.setdefault(w.series, []).append(i)
    pos_all = list(range(len(cache)))

    tasks = build_tasks()
    print(f"windows: {len(cache)}  tasks: {len(tasks)}")

    ctx = get_context("spawn")
    results = []
    with ctx.Pool(processes=8) as pool:
        for t in tasks:
            pd = {k: v for k, v in asdict(t["params"]).items()}
            if t.get("series_filter"):
                idxs = [i for s in t["series_filter"] for i in pos_by_series.get(s, [])]
            else:
                idxs = pos_all
            shards = [idxs[k:k + 400] for k in range(0, len(idxs), 400)]
            res = pool.map(_worker, [(sh, pd, t["chase_cap"],
                                      t.get("entry_delay_sec", 0.0)) for sh in shards])
            rows = [r for chunk in res for r in chunk]
            s = summarize(rows, size=5, n_boot=2000, seed=stable_seed(t["name"]),
                          settle_correct=True)
            s["name"] = t["name"]
            s["series_filter"] = t.get("series_filter")
            s["entry_delay_sec"] = t.get("entry_delay_sec", 0.0)
            results.append(s)
            print(f"  simmed {t['name']} (n={len(rows)})")

    # `0.0 or -9e9` is -9e9, so a break-even config sorted below every
    # loss. Test for None explicitly (issue #182).
    results.sort(key=lambda r: -(r["total_pnl_usd"]
                                 if r.get("total_pnl_usd") is not None else -9e9))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, default=str)

    print(f"\n{'config':<36}{'n':>5}{'pairs':>7}{'exits':>7}{'win%':>6}"
          f"{'tot$':>9}{'mean c':>8}{'ci_lo':>8}{'day_lo':>8}{'PF':>6}")
    for r in results:
        d = r.get("ci95_day_lo")
        print(f"{r['name']:<36}{r['n']:>5}{r['pair_rate']*100:>6.1f}%{r['exit_rate']*100:>6.1f}%"
              f"{r['win_rate']*100:>5.1f}%{r['total_pnl_usd']:>+9.2f}{r['mean_net_cents']:>+8.2f}"
              f"{r['ci95_lo']:>+8.2f}{(d if d is not None else float('nan')):>+8.2f}"
              f"{r['profit_factor']:>6.2f}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
