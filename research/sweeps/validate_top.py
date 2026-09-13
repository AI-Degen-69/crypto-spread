"""Trade-level validation of the top phase-5 configs.

For each config: per-day totals, filled-window count, trades-only bootstrap CI
(resample filled windows), and per-day counts — the honest view when fills are
sparse (window-level CIs are dominated by zeros).
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from dataclasses import asdict, replace
from pathlib import Path
from multiprocessing import get_context

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams  # noqa: E402
from ev_lab import _get_cache, default_base_params, bootstrap_ci  # noqa: E402
from sim2 import sim2  # noqa: E402

TOP = [
    ("off=0.02_band=0.06_d=60_ch", 0.02, 0.06, 60.0, 0.98, 0.08),
    ("off=0.03_band=0.03_d=60_nc_ex=none", 0.03, 0.03, 60.0, 0.0, 0.49),
    ("off=0.03_band=0.04_d=60_nc_ex=none", 0.03, 0.04, 60.0, 0.0, 0.49),
    ("off=0.02_band=none_d=60_ch", 0.02, 0.0, 60.0, 0.98, 0.08),
    ("off=0.03_band=0.06_d=60_ch", 0.03, 0.06, 60.0, 0.98, 0.08),
]


def exit_dict(e5: float) -> dict:
    if e5 >= 0.49:
        return {k: e5 for k in ("default_5m", "default_15m", "btc-up-or-down-5m",
                                "sol-up-or-down-5m", "btc-up-or-down-15m",
                                "sol-up-or-down-15m")}
    return {
        "default_5m": e5, "default_15m": round(e5 + 0.01, 2),
        "btc-up-or-down-5m": max(0.03, round(e5 - 0.03, 2)),
        "sol-up-or-down-5m": max(0.04, round(e5 - 0.01, 2)),
        "btc-up-or-down-15m": round(e5 + 0.01, 2),
        "sol-up-or-down-15m": round(e5 + 0.01, 2),
    }


def _worker(args):
    idxs, pd, chase, delay, band = args
    from ev_lab import load_cache
    cache = load_cache()
    params = BacktestParams(**pd)
    return [sim2(cache[i], params, chase_cap=chase or None,
                 entry_delay_sec=delay or 0.0, entry_band=band if band > 0 else None)
            for i in idxs]


def main() -> int:
    cache = _get_cache()
    pos_all = list(range(len(cache)))
    ctx = get_context("spawn")
    out = {}
    with ctx.Pool(processes=5) as pool:
        for name, off, band, delay, chase, ex in TOP:
            pd = {k: v for k, v in asdict(replace(
                default_base_params(), offset=off, fill_model="tape",
                exit_thresh_by_slug=exit_dict(ex))).items()}
            shards = [pos_all[k:k + 400] for k in range(0, len(pos_all), 400)]
            res = pool.map(_worker, [(sh, pd, chase, delay, band) for sh in shards])
            rows = [r for chunk in res for r in chunk]
            net = [(r["pnl"] - r["fees"]) * 5 for r in rows]
            filled = [(r["pnl"] - r["fees"]) * 5 for r in rows
                      if r["filled_up"] or r["filled_dn"]]
            by_day: dict[str, list[float]] = {}
            by_day_filled: dict[str, int] = {}
            for r, v in zip(rows, net):
                by_day.setdefault(r["day"], []).append(v)
                if r["filled_up"] or r["filled_dn"]:
                    by_day_filled[r["day"]] = by_day_filled.get(r["day"], 0) + 1
            lo, hi, mean, dlo, dhi = bootstrap_ci(net, n_boot=5000, seed=11,
                                                  clusters=[r["day"] for r in rows])
            tlo, thi, tmean, _dlo, _dhi = bootstrap_ci(filled, n_boot=5000, seed=12)
            # per-day sign
            day_rows = []
            for d in sorted(by_day):
                vals = by_day[d]
                day_rows.append({
                    "day": d, "windows": len(vals), "filled": by_day_filled.get(d, 0),
                    "total_usd": sum(vals) / 100.0,
                    "mean_c": statistics.fmean(vals),
                    "pnl_usd_pos": sum(vals) > 0,
                })
            out[name] = {
                "windows": len(rows), "filled_windows": len(filled),
                "mean_all_c": mean, "ci95_all_lo": lo, "ci95_all_hi": hi,
                "day_cluster_lo": dlo,
                "mean_filled_c": tmean if filled else 0.0,
                "ci95_filled_lo": tlo if filled else None,
                "total_usd": sum(net) / 100.0,
                "by_day": day_rows,
            }
            print(f"\n=== {name}")
            print(f"  windows={len(rows)} filled={len(filled)} total={sum(net)/100:+.2f}$")
            print(f"  mean/window={mean:+.3f}c CI95=[{lo:+.3f},{hi:+.3f}] day_lo={dlo}")
            if filled:
                print(f"  mean/trade={tmean:+.2f}c CI95=[{tlo:+.2f},{thi:+.2f}] n={len(filled)}")
            for d in day_rows:
                print(f"    {d['day']}: n={d['windows']} filled={d['filled']} "
                      f"tot={d['total_usd']:+8.2f}$ mean={d['mean_c']:+6.2f}c "
                      f"{'POS' if d['pnl_usd_pos'] else 'NEG'}")

    with open(Path(__file__).parent / "validate_top.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
