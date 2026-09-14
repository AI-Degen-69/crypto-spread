"""Phase 6: top band+delay configs under queue-aware tapeq fills.

The tape model treats any print at our resting price as a fill (no queue
position). tapeq requires the queue ahead (snapshotted at entry) to be
consumed by printed size, or a trade to go strictly through us. If the edge
survives tapeq, it is robust to queue realism; if not, it lives in the
tape-vs-tapeq gap and needs live queue telemetry before sizing.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams  # noqa: E402
from ev_lab import _get_cache, summarize, default_base_params, sweep_pool  # noqa: E402
from sim2 import sim2  # noqa: E402

TOP = [
    ("tq_off=0.03_band=0.04_d=60_ex=none", 0.03, 0.04, 60.0, 0.0, 0.49),
    ("tq_off=0.03_band=0.03_d=60_ex=none", 0.03, 0.03, 60.0, 0.0, 0.49),
    ("tq_off=0.02_band=0.06_d=60_ch", 0.02, 0.06, 60.0, 0.98, 0.08),
    ("tq_off=0.02_band=none_d=60_ch", 0.02, 0.0, 60.0, 0.98, 0.08),
    ("tq_off=0.03_band=0.04_d=60_ch_ex=none", 0.03, 0.04, 60.0, 0.98, 0.49),
]


def exit_dict(ex: float) -> dict:
    if ex >= 0.49:
        return {k: ex for k in ("default_5m", "default_15m", "btc-up-or-down-5m",
                                "sol-up-or-down-5m", "btc-up-or-down-15m",
                                "sol-up-or-down-15m")}
    return {
        "default_5m": ex, "default_15m": round(ex + 0.01, 2),
        "btc-up-or-down-5m": max(0.03, round(ex - 0.03, 2)),
        "sol-up-or-down-5m": max(0.04, round(ex - 0.01, 2)),
        "btc-up-or-down-15m": round(ex + 0.01, 2),
        "sol-up-or-down-15m": round(ex + 0.01, 2),
    }


def _worker(args):
    idxs, pd, chase, delay, band = args
    # `_get_cache` memoises in a process-level global; `load_cache`
    # re-unpickles ~336MB per call, and pool.map dispatches one task
    # per shard per config (issue #182).
    from ev_lab import _get_cache
    cache = _get_cache()
    params = BacktestParams(**pd)
    return [sim2(cache[i], params, chase_cap=chase or None,
                 entry_delay_sec=delay or 0.0, entry_band=band if band > 0 else None)
            for i in idxs]


def main() -> int:
    cache = _get_cache()
    pos_all = list(range(len(cache)))
    results = []
    with sweep_pool(5) as pool:
        for name, off, band, delay, chase, ex in TOP:
            pd = {k: v for k, v in asdict(replace(
                default_base_params(), offset=off, fill_model="tapeq",
                exit_thresh_by_slug=exit_dict(ex))).items()}
            shards = [pos_all[k:k + 400] for k in range(0, len(pos_all), 400)]
            res = pool.map(_worker, [(sh, pd, chase, delay, band) for sh in shards])
            rows = [r for chunk in res for r in chunk]
            s = summarize(rows, size=5, n_boot=4000, seed=21, settle_correct=True)
            s["name"] = name
            filled = [(r["pnl"] - r["fees"]) * 5 for r in rows
                      if r["filled_up"] or r["filled_dn"]]
            s["filled_windows"] = len(filled)
            s["filled_total_usd"] = sum(filled) / 100.0
            by_day: dict[str, float] = {}
            for r, v in zip(rows, [(r["pnl"] - r["fees"]) * 5 for r in rows]):
                by_day[r["day"]] = by_day.get(r["day"], 0.0) + v
            s["by_day_usd"] = {k: round(v / 100.0, 2) for k, v in sorted(by_day.items())}
            results.append(s)
            print(f"  simmed {name}")

    # `0.0 or -9e9` is -9e9, so a break-even config sorted below every
    # loss. Test for None explicitly (issue #182).
    results.sort(key=lambda r: -(r["total_pnl_usd"]
                                 if r.get("total_pnl_usd") is not None else -9e9))
    with open(Path(__file__).parent / "phase6_tapeq_top.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, default=str)

    print(f"\n{'config':<40}{'filled':>7}{'pairs':>7}{'win%':>6}"
          f"{'tot$':>9}{'mean c':>8}{'ci_lo':>8}{'day_lo':>8}{'PF':>6}")
    for r in results:
        d = r.get("ci95_day_lo")
        print(f"{r['name']:<40}{r['filled_windows']:>7}{r['pair_rate']*100:>6.1f}%"
              f"{r['win_rate']*100:>5.1f}%{r['total_pnl_usd']:>+9.2f}{r['mean_net_cents']:>+8.2f}"
              f"{r['ci95_lo']:>+8.2f}{(d if d is not None else float('nan')):>+8.2f}"
              f"{r['profit_factor']:>6.2f}")
        print(f"    by day: {r['by_day_usd']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
