"""Automated parameter sweep and grid search runner for SPREAD-2 backtests.

Evaluates multiple BacktestParams combinations across collected tick data,
computes risk/return performance metrics, ranks strategy profiles,
and identifies the optimal parameter set with positive expected value.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from backtest.engine import (
    BacktestParams,
    WindowResult,
    _simulate_window,
    group_by_cid,
    iter_ticks,
)

DEFAULT_TICKS = Path(__file__).resolve().parent.parent / "run" / "ticks"


@dataclass
class SweepRunResult:
    """Summary metrics for a single parameter configuration."""
    params: BacktestParams
    param_label: str
    n_windows: int
    pair_rate: float
    exit_rate: float
    win_rate: float
    total_pnl_cents: float
    avg_pnl_cents: float
    total_fees_cents: float
    max_drawdown_cents: float
    profit_factor: float
    sharpe_proxy: float
    per_series_pnl: dict[str, float]

    def to_dict(self) -> dict:
        """Serialize run result to dictionary."""
        d = asdict(self)
        d["params"] = asdict(self.params)
        return d


def compute_metrics(
    window_results: list[WindowResult],
    params: BacktestParams,
    label: str,
    size: int = 5,
) -> SweepRunResult:
    """Calculate summary and risk metrics across window replay results scaled by position size."""
    size = max(5, int(size))
    n = len(window_results)
    if n == 0:
        return SweepRunResult(
            params=params,
            param_label=label,
            n_windows=0,
            pair_rate=0.0,
            exit_rate=0.0,
            win_rate=0.0,
            total_pnl_cents=0.0,
            avg_pnl_cents=0.0,
            total_fees_cents=0.0,
            max_drawdown_cents=0.0,
            profit_factor=0.0,
            sharpe_proxy=0.0,
            per_series_pnl={},
        )

    pairs = sum(1 for w in window_results if w.pair_captured)
    exits = sum(1 for w in window_results if w.exit_taken)
    pnls = [(w.pnl_cents - w.fees_cents) * size for w in window_results]
    wins = sum(1 for p in pnls if p > 0)
    total_pnl = sum(pnls)
    total_fees = sum(w.fees_cents for w in window_results) * size

    # Max Drawdown & PnL standard deviation scaled by size
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0

    gross_gains = sum(p for p in pnls if p > 0)
    gross_losses = abs(sum(p for p in pnls if p < 0))
    profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else (999.0 if gross_gains > 0 else 0.0)

    for p in pnls:
        cum_pnl += p
        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        dd = peak_pnl - cum_pnl
        if dd > max_dd:
            max_dd = dd

    std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
    mean_pnl = total_pnl / n
    sharpe_proxy = (mean_pnl / std_pnl) * math.sqrt(n) if std_pnl > 0 else 0.0

    per_series_pnl: dict[str, float] = {}
    for w in window_results:
        net_w = (w.pnl_cents - w.fees_cents) * size
        per_series_pnl[w.series] = round(per_series_pnl.get(w.series, 0.0) + net_w, 2)

    return SweepRunResult(
        params=params,
        param_label=label,
        n_windows=n,
        pair_rate=round(pairs / n, 4),
        exit_rate=round(exits / n, 4),
        win_rate=round(wins / n, 4),
        total_pnl_cents=round(total_pnl, 2),
        avg_pnl_cents=round(mean_pnl, 4),
        total_fees_cents=round(total_fees, 2),
        max_drawdown_cents=round(max_dd, 2),
        profit_factor=round(profit_factor, 2),
        sharpe_proxy=round(sharpe_proxy, 2),
        per_series_pnl=per_series_pnl,
    )


def generate_sensitivity_grid(
    base_params: BacktestParams | None = None,
    size: int = 5,
) -> list[tuple[str, BacktestParams]]:
    """Generate 1D sensitivity parameter variations against a fixed baseline."""
    size = max(5, int(size))
    base = base_params or BacktestParams(quote_shares=size)
    grid: list[tuple[str, BacktestParams]] = []

    # 1. Baseline
    grid.append(("Baseline", base))

    # 2. Offset variations (1.0c to 4.0c)
    offsets = [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040]
    for off in offsets:
        if off != base.offset:
            p = BacktestParams(
                offset=off,
                queue_gate=base.queue_gate,
                pair_cost_gate=base.pair_cost_gate,
                exit_thresh_by_slug=base.exit_thresh_by_slug,
                exit_reversal=base.exit_reversal,
                quote_shares=base.quote_shares,
                fill_model=base.fill_model,
                merge_gas_usd=base.merge_gas_usd,
                taker_fee_rate=base.taker_fee_rate,
                max_start_delay_sec=base.max_start_delay_sec,
            )
            grid.append((f"offset={off:.3f}", p))

    # 3. Queue gate variations
    queues = [0.0, 10.0, 25.0, 50.0, 100.0, 200.0]
    for q in queues:
        if q != base.queue_gate:
            p = BacktestParams(
                offset=base.offset,
                queue_gate=q,
                pair_cost_gate=base.pair_cost_gate,
                exit_thresh_by_slug=base.exit_thresh_by_slug,
                exit_reversal=base.exit_reversal,
                quote_shares=base.quote_shares,
                fill_model=base.fill_model,
                merge_gas_usd=base.merge_gas_usd,
                taker_fee_rate=base.taker_fee_rate,
                max_start_delay_sec=base.max_start_delay_sec,
            )
            grid.append((f"queue={q:.0f}", p))

    # 4. Exit threshold variations
    exit_5m_levels = [0.06, 0.08, 0.10, 0.12, 0.14, 0.16]
    for e in exit_5m_levels:
        ex_dict = dict(base.exit_thresh_by_slug)
        ex_dict["default_5m"] = e
        ex_dict["btc-up-or-down-5m"] = max(0.05, e - 0.03)
        ex_dict["sol-up-or-down-5m"] = max(0.06, e - 0.01)
        p = BacktestParams(
            offset=base.offset,
            queue_gate=base.queue_gate,
            pair_cost_gate=base.pair_cost_gate,
            exit_thresh_by_slug=ex_dict,
            exit_reversal=base.exit_reversal,
            quote_shares=base.quote_shares,
            fill_model=base.fill_model,
            merge_gas_usd=base.merge_gas_usd,
            taker_fee_rate=base.taker_fee_rate,
            max_start_delay_sec=base.max_start_delay_sec,
        )
        grid.append((f"exit_5m={e:.2f}", p))

    # 5. Exit reversal variations
    reversals = [0.010, 0.015, 0.020, 0.030]
    for r in reversals:
        if r != base.exit_reversal:
            p = BacktestParams(
                offset=base.offset,
                queue_gate=base.queue_gate,
                pair_cost_gate=base.pair_cost_gate,
                exit_thresh_by_slug=base.exit_thresh_by_slug,
                exit_reversal=r,
                quote_shares=base.quote_shares,
                fill_model=base.fill_model,
                merge_gas_usd=base.merge_gas_usd,
                taker_fee_rate=base.taker_fee_rate,
                max_start_delay_sec=base.max_start_delay_sec,
            )
            grid.append((f"exit_rev={r:.3f}", p))

    # 6. Pair cost gate variations
    pair_costs = [1.01, 1.02, 1.03, 1.05, 1.10]
    for pc in pair_costs:
        if pc != base.pair_cost_gate:
            p = BacktestParams(
                offset=base.offset,
                queue_gate=base.queue_gate,
                pair_cost_gate=pc,
                exit_thresh_by_slug=base.exit_thresh_by_slug,
                exit_reversal=base.exit_reversal,
                quote_shares=base.quote_shares,
                fill_model=base.fill_model,
                merge_gas_usd=base.merge_gas_usd,
                taker_fee_rate=base.taker_fee_rate,
                max_start_delay_sec=base.max_start_delay_sec,
            )
            grid.append((f"pair_cost={pc:.2f}", p))

    return grid


def generate_joint_grid(
    offsets: Sequence[float] = (0.015, 0.020, 0.025, 0.030),
    queues: Sequence[float] = (0.0, 25.0, 50.0, 100.0),
    exit_5ms: Sequence[float] = (0.08, 0.10, 0.12, 0.14),
    exit_reversals: Sequence[float] = (0.015, 0.020),
    fill_model: str = "tape",
    max_start_delay: float = 0.0,
    size: int = 5,
) -> list[tuple[str, BacktestParams]]:
    """Generate multi-dimensional Cartesian grid across controllable parameters."""
    size = max(5, int(size))
    grid: list[tuple[str, BacktestParams]] = []
    for off, q, e5, rev in itertools.product(offsets, queues, exit_5ms, exit_reversals):
        ex_dict = {
            "default_5m": e5,
            "default_15m": round(e5 + 0.01, 2),
            "btc-up-or-down-5m": max(0.05, round(e5 - 0.03, 2)),
            "sol-up-or-down-5m": max(0.06, round(e5 - 0.01, 2)),
            "btc-up-or-down-15m": round(e5 + 0.01, 2),
            "sol-up-or-down-15m": round(e5 + 0.01, 2),
        }
        label = f"off={off:.3f}_q={q:.0f}_ex={e5:.2f}_rev={rev:.3f}"
        p = BacktestParams(
            offset=off,
            queue_gate=q,
            pair_cost_gate=1.05,
            exit_thresh_by_slug=ex_dict,
            exit_reversal=rev,
            quote_shares=size,
            fill_model=fill_model,
            merge_gas_usd=0.0,
            taker_fee_rate=0.07,
            max_start_delay_sec=max_start_delay,
        )
        grid.append((label, p))
    return grid


def generate_random_grid(
    count: int = 50,
    seed: int = 42,
    fill_model: str = "tape",
    max_start_delay: float = 0.0,
    size: int = 5,
) -> list[tuple[str, BacktestParams]]:
    """Sample random parameter configurations from declared ranges with a deterministic seed."""
    rng = random.Random(seed)
    size = max(5, int(size))
    offsets = [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040]
    queues = [0.0, 10.0, 25.0, 50.0, 100.0, 200.0]
    exit_5ms = [0.06, 0.08, 0.09, 0.10, 0.11, 0.12, 0.14, 0.16]
    exit_reversals = [0.010, 0.015, 0.020, 0.030]
    pair_costs = [1.01, 1.02, 1.03, 1.05, 1.10]

    grid: list[tuple[str, BacktestParams]] = []
    seen: set[tuple[float, float, float, float, float]] = set()
    for _ in range(count * 5):
        if len(grid) >= count:
            break
        off = rng.choice(offsets)
        q = rng.choice(queues)
        e5 = rng.choice(exit_5ms)
        rev = rng.choice(exit_reversals)
        pc = rng.choice(pair_costs)
        key = (off, q, e5, rev, pc)
        if key in seen:
            continue
        seen.add(key)

        ex_dict = {
            "default_5m": e5,
            "default_15m": round(e5 + 0.01, 2),
            "btc-up-or-down-5m": max(0.05, round(e5 - 0.03, 2)),
            "sol-up-or-down-5m": max(0.06, round(e5 - 0.01, 2)),
            "btc-up-or-down-15m": round(e5 + 0.01, 2),
            "sol-up-or-down-15m": round(e5 + 0.01, 2),
        }
        label = f"rand_off={off:.3f}_q={q:.0f}_ex={e5:.2f}_rev={rev:.3f}"
        p = BacktestParams(
            offset=off,
            queue_gate=q,
            pair_cost_gate=pc,
            exit_thresh_by_slug=ex_dict,
            exit_reversal=rev,
            quote_shares=size,
            fill_model=fill_model,
            merge_gas_usd=0.0,
            taker_fee_rate=0.07,
            max_start_delay_sec=max_start_delay,
        )
        grid.append((label, p))
    return grid


def run_sweep(
    grouped_windows: list[tuple[str, list[dict]]],
    grid: list[tuple[str, BacktestParams]],
    series_whitelist: set[str] | None = None,
    size: int = 5,
) -> list[SweepRunResult]:
    """Execute parameter sweep against pre-grouped condition windows."""
    size = max(5, int(size))
    results: list[SweepRunResult] = []

    # Filter windows by series if whitelist provided
    filtered_windows = grouped_windows
    if series_whitelist:
        filtered_windows = [
            (cid, snaps) for cid, snaps in grouped_windows
            if snaps and snaps[0].get("series") in series_whitelist
        ]

    for label, params in grid:
        window_results: list[WindowResult] = []
        for _cid, snaps in filtered_windows:
            if not snaps:
                continue
            if params.max_start_delay_sec > 0:
                first_ts = float(snaps[0].get("ts", 0.0) or 0.0)
                start_ts = float(snaps[0].get("start_ts", 0.0) or 0.0)
                delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
                if delay > params.max_start_delay_sec:
                    continue
            w_res = _simulate_window(snaps, params)
            window_results.append(w_res)

        metrics = compute_metrics(window_results, params, label=label, size=size)
        results.append(metrics)

    return results


def format_markdown_table(results: list[SweepRunResult], top_n: int = 15) -> str:
    """Format top sweep results as a clean Markdown table."""
    sorted_res = sorted(results, key=lambda r: r.total_pnl_cents, reverse=True)[:top_n]
    lines: list[str] = [
        "| Rank | Configuration | PnL (cents) | Avg PnL | Win Rate | Pair Rate | Exit Rate | Max DD | Profit Factor | Sharpe |",
        "|:---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(sorted_res, 1):
        lines.append(
            f"| {i} | `{r.param_label}` | {r.total_pnl_cents:+.2f}c | "
            f"{r.avg_pnl_cents:+.2f}c | {r.win_rate * 100:.1f}% | "
            f"{r.pair_rate * 100:.1f}% | {r.exit_rate * 100:.1f}% | "
            f"{r.max_drawdown_cents:.2f}c | {r.profit_factor:.2f} | {r.sharpe_proxy:.2f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for backtest parameter sweep engine."""
    ap = argparse.ArgumentParser(description="SPREAD-2 Quant Parameter Sweep Runner")
    ap.add_argument("source", nargs="?", default=str(DEFAULT_TICKS),
                    help="ticks directory or .jsonl[.gz] file")
    ap.add_argument("--preset", choices=["sensitivity", "grid", "assets", "random"], default="sensitivity",
                    help="Sweep preset: sensitivity (1D), grid (joint), assets (universe), random (stochastic)")
    ap.add_argument("--count", type=int, default=50,
                    help="Sample count for random sweep (default: 50)")
    ap.add_argument("--seed", type=int, default=42,
                    help="Deterministic seed for random sweep (default: 42)")
    ap.add_argument("--fill-model", choices=["tape", "book", "both", "cross"], default="tape",
                    help="Fill model: tape (conservative), book (optimistic), cross (strict price-crossing)")
    ap.add_argument("--size", type=int, default=5,
                    help="Position size in shares (minimum 5, default: 5)")
    ap.add_argument("--top", type=int, default=15, help="Number of top configurations to show")
    ap.add_argument("--max-start-delay", type=float, default=0.0,
                    help="Filter late-started windows (> N seconds)")
    ap.add_argument("--filter-partial", action="store_true",
                    help="Shorthand to filter late windows > 5s")
    ap.add_argument("--series", type=str, default="",
                    help="Comma-separated series whitelist (e.g. btc-up-or-down-5m,eth-up-or-down-5m)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Optional JSON output file path")
    args = ap.parse_args(argv)

    size = max(5, args.size)
    max_delay = args.max_start_delay
    if args.filter_partial and max_delay <= 0:
        max_delay = 5.0

    whitelist = set(s.strip() for s in args.series.split(",") if s.strip()) if args.series else None

    print(f"Loading ticks from {args.source}...")
    t0 = time.perf_counter()
    snaps = list(iter_ticks(args.source))
    if not snaps:
        print("Error: No ticks loaded.", file=sys.stderr)
        return 1
    t_load = time.perf_counter() - t0
    print(f"Loaded {len(snaps)} snaps in {t_load:.2f}s. Grouping windows...")

    grouped = group_by_cid(snaps)
    print(f"Grouped into {len(grouped)} condition windows. Running '{args.preset}' sweep (size={size} shares)...")

    # Build grid based on preset
    base = BacktestParams(fill_model=args.fill_model, max_start_delay_sec=max_delay, quote_shares=size)

    if args.preset == "sensitivity":
        grid = generate_sensitivity_grid(base, size=size)
    elif args.preset == "grid":
        grid = generate_joint_grid(fill_model=args.fill_model, max_start_delay=max_delay, size=size)
    elif args.preset == "random":
        grid = generate_random_grid(
            count=args.count,
            seed=args.seed,
            fill_model=args.fill_model,
            max_start_delay=max_delay,
            size=size,
        )
    elif args.preset == "assets":
        # Asset whitelist sweep
        all_series = {
            "btc-up-or-down-5m", "btc-up-or-down-15m",
            "eth-up-or-down-5m", "eth-up-or-down-15m",
            "sol-up-or-down-5m", "sol-up-or-down-15m",
            "bnb-up-or-down-5m", "bnb-up-or-down-15m",
            "xrp-up-or-down-5m", "xrp-up-or-down-15m",
        }
        asset_configs = [
            ("All 10 Series", all_series),
            ("Top-3 (BTC, ETH, SOL 5m+15m)", {s for s in all_series if any(k in s for k in ("btc", "eth", "sol"))}),
            ("5m Only (All)", {s for s in all_series if "-5m" in s}),
            ("15m Only (All)", {s for s in all_series if "-15m" in s}),
            ("BTC + ETH Only", {s for s in all_series if "btc" in s or "eth" in s}),
            ("SOL Only", {s for s in all_series if "sol" in s}),
        ]
        grid = []
        for name, _s_set in asset_configs:
            grid.append((name, base))

    t_sweep_start = time.perf_counter()
    if args.preset == "assets":
        results = []
        for name, s_set in asset_configs:
            res = run_sweep(grouped, [(name, base)], series_whitelist=s_set, size=size)
            results.extend(res)
    else:
        results = run_sweep(grouped, grid, series_whitelist=whitelist, size=size)
    t_sweep = time.perf_counter() - t_sweep_start

    print(f"Completed {len(results)} backtest runs in {t_sweep:.2f}s "
          f"({len(results) / max(0.001, t_sweep):.1f} runs/s).\n")

    print(f"### Top {args.top} Parameter Configurations for Position Size = {size} Shares (Ranked by Total PnL):\n")
    table = format_markdown_table(results, top_n=args.top)
    print(table)

    if args.out:
        out_payload = {
            "source": str(args.source),
            "preset": args.preset,
            "fill_model": args.fill_model,
            "size": size,
            "count": args.count if args.preset == "random" else None,
            "seed": args.seed if args.preset == "random" else None,
            "n_runs": len(results),
            "runs": [r.to_dict() for r in sorted(results, key=lambda x: x.total_pnl_cents, reverse=True)],
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out_payload, f, indent=2)
        print(f"\nWrote full sweep results to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
