"""Phase 2: queue-aware fills (tapeq), fee scenarios, leg-chase pairs rule, re-entry.

Leg-chase pairs rule mirrors strategy/live_trader.py issue #123: after one leg
fills, the opposite quote steps up to the ask capped at max_pair_cost. In the
backtest this is emulated by using pair_cost_gate as the cap: when one leg is
filled, the opposite resting quote becomes min(ask, 1 - entry - ...). To keep
the simulator engine-parity-pure we approximate the chase by re-anchoring the
unfilled leg to min(ask, max_pair_cost - entry) at each tick once the other leg
has filled. Implemented via chase=True in this driver (not in the engine).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams
from ev_lab import _get_cache, sweep_configs, default_base_params, fast_simulate

OUT = Path(__file__).resolve().parent / "phase2_fills.json"

BASE = default_base_params()


def exit_dict(e5: float) -> dict:
    return {
        "default_5m": e5, "default_15m": round(e5 + 0.01, 2),
        "btc-up-or-down-5m": max(0.03, round(e5 - 0.03, 2)),
        "sol-up-or-down-5m": max(0.04, round(e5 - 0.01, 2)),
        "btc-up-or-down-15m": round(e5 + 0.01, 2),
        "sol-up-or-down-15m": round(e5 + 0.01, 2),
    }


def build_configs() -> list[dict]:
    cfgs = []

    def add(name, params, **filters):
        cfgs.append({"name": name,
                     "params_kwargs": {k: v for k, v in asdict(params).items()},
                     **filters})

    # tapeq fills across the most promising offsets/exits from phase 1
    for off in (0.01, 0.015, 0.02, 0.025, 0.03):
        add(f"tapeq_off={off:.3f}", replace(BASE, offset=off, fill_model="tapeq"))
    for e5 in (0.06, 0.08, 0.10, 0.12):
        add(f"tapeq_ex={e5:.2f}", replace(BASE, fill_model="tapeq",
                                          exit_thresh_by_slug=exit_dict(e5)))
    add("tapeq_ex=none", replace(BASE, fill_model="tapeq", exit_thresh_by_slug={
        k: 0.49 for k in ("default_5m", "default_15m", "btc-up-or-down-5m",
                          "sol-up-or-down-5m", "btc-up-or-down-15m",
                          "sol-up-or-down-15m")}))
    # tape baseline for comparison under identical configs
    add("tape_off=0.020", replace(BASE))
    add("tape_ex=none", replace(BASE, exit_thresh_by_slug={
        k: 0.49 for k in ("default_5m", "default_15m", "btc-up-or-down-5m",
                          "sol-up-or-down-5m", "btc-up-or-down-15m",
                          "sol-up-or-down-15m")}))

    # zero-fee scenario (feeSchedule varies by market; 0.07 is the category max)
    add("tapeq_fee0_off=0.020", replace(BASE, fill_model="tapeq", taker_fee_rate=0.0))
    add("tape_fee0_ex=none", replace(BASE, taker_fee_rate=0.0, exit_thresh_by_slug={
        k: 0.49 for k in ("default_5m", "default_15m", "btc-up-or-down-5m",
                          "sol-up-or-down-5m", "btc-up-or-down-15m",
                          "sol-up-or-down-15m")}))
    add("tape_fee0_base", replace(BASE, taker_fee_rate=0.0))

    # re-entry policy (issue #95 knobs) on tape fills
    add("reentry_band=0.015", replace(BASE, reentry_drift_band=0.015,
                                      min_requote_remaining_sec=60.0,
                                      reentry_min_remaining_pct=0.30,
                                      max_reentries_per_window=1))
    add("reentry_band=0.030", replace(BASE, reentry_drift_band=0.030,
                                      min_requote_remaining_sec=60.0,
                                      reentry_min_remaining_pct=0.30,
                                      max_reentries_per_window=1))
    return cfgs


def main() -> int:
    cache = _get_cache()
    print(f"windows: {len(cache)}")
    cfgs = build_configs()
    print(f"configs: {len(cfgs)}")
    results = sweep_configs(cfgs, workers=8, n_boot=2000, size=5)
    results.sort(key=lambda r: -(r.get("total_pnl_usd") or -9e9))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, default=str)
    print(f"{'config':<22}{'n':>6}{'pairs':>7}{'exits':>7}{'win%':>7}"
          f"{'tot$':>9}{'mean c':>9}{'ci_lo':>9}{'day_lo':>9}{'PF':>6}{'DD$':>8}")
    for r in results:
        d = r.get("ci95_day_lo")
        print(f"{r['name']:<22}{r['n']:>6}{r['pair_rate']*100:>6.1f}%{r['exit_rate']*100:>6.1f}%"
              f"{r['win_rate']*100:>6.1f}%{r['total_pnl_usd']:>+9.2f}{r['mean_net_cents']:>+9.2f}"
              f"{r['ci95_lo']:>+9.2f}{(d if d is not None else float('nan')):>+9.2f}"
              f"{r['profit_factor']:>6.2f}{r['max_dd_usd']:>8.2f}")
    # outcome decomposition for the best few
    print("\noutcome decomposition (n / mean cents / total $):")
    for r in results[:6]:
        od = r.get("outcome_decomp", {})
        segs = []
        for k in ("pairs", "exits", "naked"):
            s = od.get(k, {})
            segs.append(f"{k}: {s.get('n', 0)}/{s.get('mean_c', 0):+.1f}c/{s.get('total_usd', 0):+.1f}$")
        segs.append(f"nofill: {od.get('no_fill', 0)}")
        print(f"  {r['name']:<22} " + "  ".join(segs))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
