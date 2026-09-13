"""Phase 1: 1D sensitivity sweeps across the full 2,4xx-window dataset.

Axes: offset, queue_gate, exit_5m (15m = +1c), exit_reversal, pair_cost_gate,
entry_timeout_pct, fill_model. Baseline = repo-optimal (docs/backtest-optimization-results.md):
offset=0.02, queue=0, exit_5m=0.08, rev=0.015, pair_cost=1.05, tape.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams
from ev_lab import _get_cache, sweep_configs, default_base_params

OUT = Path(__file__).resolve().parent / "phase1_1d.json"

BASE = default_base_params()  # offset .02, q0, pc1.05, tape, no timeout, no reentry


def exit_dict(e5: float, rev_note: str = "") -> dict:
    """Repo joint-grid convention: 15m = e5+0.01, BTC 5m tightest, SOL -1c."""
    return {
        "default_5m": e5,
        "default_15m": round(e5 + 0.01, 2),
        "btc-up-or-down-5m": max(0.03, round(e5 - 0.03, 2)),
        "sol-up-or-down-5m": max(0.04, round(e5 - 0.01, 2)),
        "btc-up-or-down-15m": round(e5 + 0.01, 2),
        "sol-up-or-down-15m": round(e5 + 0.01, 2),
    }


def build_configs() -> list[dict]:
    cfgs: list[dict] = []

    def add(name: str, params: BacktestParams, **filters):
        cfgs.append({"name": name, "params_kwargs": {
            k: v for k, v in asdict(params).items()
        }, **filters})

    add("BASE_repo_opt", BASE)

    # offset axis
    for off in (0.005, 0.01, 0.015, 0.025, 0.03, 0.035, 0.04, 0.05):
        add(f"off={off:.3f}", replace(BASE, offset=off))
    # queue axis
    for q in (10.0, 25.0, 50.0, 100.0, 200.0):
        add(f"q={q:.0f}", replace(BASE, queue_gate=q))
    # exit_5m axis (15m = +1c)
    for e5 in (0.04, 0.05, 0.06, 0.07, 0.09, 0.10, 0.11, 0.12, 0.14, 0.16, 0.20):
        add(f"ex={e5:.2f}", replace(BASE, exit_thresh_by_slug=exit_dict(e5)))
    # exit_reversal axis
    for r in (0.0, 0.005, 0.01, 0.02, 0.03):
        add(f"rev={r:.3f}", replace(BASE, exit_reversal=r))
    # pair cost gate axis
    for pc in (0.0, 1.00, 1.01, 1.02, 1.03, 1.10):
        add(f"pc={pc:.2f}", replace(BASE, pair_cost_gate=pc))
    # entry timeout axis (fractions of window)
    for t in (0.05, 0.10, 0.15, 0.20, 0.30):
        add(f"timeout={t:.2f}", replace(BASE, entry_timeout_pct=t))
    # fill model gap (same baseline, three models)
    add("fill=book", replace(BASE, fill_model="book"))
    add("fill=cross", replace(BASE, fill_model="cross"))
    # flat-exit probe: exit threshold way out (effectively pairs-or-settle)
    add("ex=none", replace(BASE, exit_thresh_by_slug={
        "default_5m": 0.49, "default_15m": 0.49,
        "btc-up-or-down-5m": 0.49, "sol-up-or-down-5m": 0.49,
        "btc-up-or-down-15m": 0.49, "sol-up-or-down-15m": 0.49}))
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
    hdr = (f"{'config':<16}{'n':>6}{'pairs':>7}{'exits':>7}{'win%':>7}"
           f"{'tot$':>9}{'mean c':>9}{'ci_lo':>9}{'day_lo':>9}{'PF':>6}{'DD$':>8}")
    print(hdr)
    for r in results:
        print(f"{r['name']:<16}{r['n']:>6}{r['pair_rate']*100:>6.1f}%{r['exit_rate']*100:>6.1f}%"
              f"{r['win_rate']*100:>6.1f}%{r['total_pnl_usd']:>+9.2f}{r['mean_net_cents']:>+9.2f}"
              f"{r['ci95_lo']:>+9.2f}{(r['ci95_day_lo'] if r['ci95_day_lo'] is not None else float('nan')):>+9.2f}"
              f"{r['profit_factor']:>6.2f}{r['max_dd_usd']:>8.2f}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
