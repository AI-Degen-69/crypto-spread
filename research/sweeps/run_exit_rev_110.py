"""Issue #110 driver: isolated exit_reversal sweep at the pinned baseline.

Baseline (per issue): offset=0.02, queue_gate=0, exit_5m=0.08,
fill_model=tape, size 5. The exit dict follows the repo's own conventions
(sensitivity exit_5m loop at e=0.08 for the 5m keys; joint-grid convention
default_15m = e5 + 0.01). Run: python -m scripts.sweep_backtest ... cannot
pin exit_5m (no CLI flag), so this driver wires the public sweep functions
directly. Output: research/sweeps/exit_reversal_110.json (same schema as --out).
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from backtest.engine import BacktestParams
from scripts.sweep_backtest import (
    filter_sensitivity_grid,
    format_markdown_table,
    generate_sensitivity_grid,
    group_by_cid,
    iter_ticks,
    run_sweep,
)

SOURCE = Path("run/ticks/ticks_2026-09-08.jsonl")
OUT = Path("research/sweeps/exit_reversal_110.json")

EXIT_BASELINE = {
    "default_5m": 0.08,
    "btc-up-or-down-5m": 0.05,   # max(0.05, 0.08 - 0.03)
    "sol-up-or-down-5m": 0.07,   # max(0.06, 0.08 - 0.01)
    "default_15m": 0.09,         # 0.08 + 0.01 (joint-grid convention)
    "btc-up-or-down-15m": 0.09,
    "sol-up-or-down-15m": 0.09,
}


def main() -> int:
    t0 = time.perf_counter()
    snaps = list(iter_ticks(str(SOURCE)))
    if not snaps:
        print("Error: No ticks loaded.", file=sys.stderr)
        return 1
    print(f"Loaded {len(snaps)} snaps in {time.perf_counter() - t0:.1f}s.")
    grouped = group_by_cid(snaps)
    print(f"Grouped into {len(grouped)} condition windows.")

    base = replace(
        BacktestParams(fill_model="tape", quote_shares=5),
        exit_thresh_by_slug=dict(EXIT_BASELINE),
    )
    assert base.offset == 0.02 and base.queue_gate == 0.0
    grid = filter_sensitivity_grid(generate_sensitivity_grid(base), "exit_rev")
    print("Grid:", [lbl for lbl, _ in grid])

    t1 = time.perf_counter()
    results = run_sweep(grouped, grid, size=5)
    print(f"Completed {len(results)} runs in {time.perf_counter() - t1:.1f}s.\n")
    print(format_markdown_table(results, top_n=10))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(SOURCE),
        "preset": "sensitivity",
        "only": "exit_rev",
        "baseline": {
            "offset": base.offset,
            "queue_gate": base.queue_gate,
            "exit_thresh_by_slug": dict(EXIT_BASELINE),
            "fill_model": base.fill_model,
            "size": 5,
        },
        "n_runs": len(results),
        "runs": [r.to_dict() for r in
                 sorted(results, key=lambda x: x.total_pnl_cents, reverse=True)],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
