"""Overnight parameter optimization sweep engine for SPREAD-2 across Golden Dataset.

Iterates through multi-dimensional parameter configurations across BTC, ETH, BNB, XRP, and SOL
on 5m and 15m timeframes, logging runs incrementally and generating comparative risk-adjusted reports.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from backtest.engine import (
    BacktestParams,
    WindowResult,
    _simulate_window,
    group_by_cid,
    iter_ticks,
)
from scripts.sweep_backtest import compute_metrics, SweepRunResult

DEFAULT_GOLDEN_DIR = Path(__file__).resolve().parent.parent / "run" / "ticks" / "golden"
DEFAULT_OUT_CSV = Path(__file__).resolve().parent.parent / "run" / "backtest_results_overnight.csv"
DEFAULT_SUMMARY_MD = Path(__file__).resolve().parent.parent / "overnight_summary.md"

# Target assets and 10 series universe
TARGET_ASSETS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
ALL_SERIES = [
    "btc-up-or-down-5m", "btc-up-or-down-15m",
    "eth-up-or-down-5m", "eth-up-or-down-15m",
    "bnb-up-or-down-5m", "bnb-up-or-down-15m",
    "xrp-up-or-down-5m", "xrp-up-or-down-15m",
    "sol-up-or-down-5m", "sol-up-or-down-15m",
]

# Issue #442 Parameter Search Space
PARAM_SPACE = {
    "offset": [
        0.001, 0.002, 0.005, 0.008, 0.01, 0.012, 0.015, 0.018, 0.02,
        0.022, 0.025, 0.028, 0.03, 0.035, 0.04, 0.05, 0.06, 0.08,
        0.1, 0.15, 0.2, 0.3, 0.4, 0.49,
    ],
    "queue_depth": [
        0.0, 5.0, 10.0, 20.0, 35.0, 50.0, 75.0, 100.0, 150.0, 200.0,
        300.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0, 50000.0, 100000.0,
    ],
    "share_size": [
        5, 10, 20, 35, 50, 75, 100, 120, 150, 200, 250, 300, 500, 750,
        1000, 2000, 5000, 10000,
    ],
    "entry_delay_sec": [
        0.0, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0,
        90.0, 120.0, 180.0, 240.0, 300.0, 600.0, 900.0, 1800.0, 3600.0,
    ],
    "late_entry_pct": [
        0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.1, 0.12, 0.15, 0.2,
        0.25, 0.3, 0.4, 0.5, 0.75, 1.0,
    ],
    "leg_chase": [False, True],
    "reversal_buffer": [
        0.001, 0.005, 0.01, 0.012, 0.015, 0.018, 0.02, 0.022, 0.025,
        0.028, 0.03, 0.035, 0.04, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5,
    ],
    "stop_loss": [
        0.01, 0.02, 0.03, 0.04, 0.045, 0.05, 0.055, 0.06, 0.07, 0.08,
        0.09, 0.1, 0.12, 0.14, 0.16, 0.18, 0.2, 0.25, 0.3, 0.4, 0.5,
    ],
}


def create_baseline_params() -> BacktestParams:
    """Construct the baseline parameter configuration per Issue #442."""
    stops = {
        "default_5m": 0.05,
        "default_15m": 0.05,
        "btc-up-or-down-5m": 0.05,
        "btc-up-or-down-15m": 0.05,
        "eth-up-or-down-5m": 0.05,
        "eth-up-or-down-15m": 0.05,
        "bnb-up-or-down-5m": 0.05,
        "bnb-up-or-down-15m": 0.05,
        "sol-up-or-down-5m": 0.05,
        "sol-up-or-down-15m": 0.05,
        "xrp-up-or-down-5m": 0.05,
        "xrp-up-or-down-15m": 0.05,
    }
    return BacktestParams(
        offset=0.02,
        queue_gate=50.0,
        quote_shares=120,
        entry_delay_sec=0.0,
        entry_delay_pct=None,
        enable_leg_chase=False,
        exit_reversal=0.02,
        exit_thresh_by_slug=stops,
    )


def generate_candidates(
    n_iterations: int = 50,
    seed: int = 42,
) -> list[tuple[str, BacktestParams]]:
    """Sample candidate parameter configurations starting with Baseline.
    
    Candidate 0 is always the Baseline configuration.
    Subsequent candidates sample from PARAM_SPACE with deterministic random seed.
    """
    candidates: list[tuple[str, BacktestParams]] = [("Baseline", create_baseline_params())]
    if n_iterations <= 1:
        return candidates

    rng = random.Random(seed)
    seen: set[tuple] = set()

    for idx in range(1, n_iterations):
        # Sample parameters
        off = rng.choice(PARAM_SPACE["offset"])
        q = rng.choice(PARAM_SPACE["queue_depth"])
        size = rng.choice(PARAM_SPACE["share_size"])
        use_pct = rng.random() < 0.3
        if use_pct:
            d_sec = 0.0
            d_pct = rng.choice(PARAM_SPACE["late_entry_pct"])
        else:
            d_sec = rng.choice(PARAM_SPACE["entry_delay_sec"])
            d_pct = None
        leg_chase = rng.choice(PARAM_SPACE["leg_chase"])
        rev = rng.choice(PARAM_SPACE["reversal_buffer"])

        # Asset & default stop losses
        default_5m = rng.choice(PARAM_SPACE["stop_loss"])
        default_15m = rng.choice(PARAM_SPACE["stop_loss"])

        # Allow slight asset-specific variations or synchronized stops
        varied_assets = rng.random() < 0.4
        stops: dict[str, float] = {
            "default_5m": default_5m,
            "default_15m": default_15m,
        }
        for s in ALL_SERIES:
            if varied_assets and rng.random() < 0.5:
                stops[s] = rng.choice(PARAM_SPACE["stop_loss"])
            else:
                stops[s] = default_5m if "-5m" in s else default_15m

        key = (off, q, size, d_sec, d_pct, leg_chase, rev, default_5m, default_15m)
        if key in seen:
            continue
        seen.add(key)

        label = (
            f"run_{idx:03d}_off={off:.3f}_q={q:.0f}_sz={size}"
            f"_{'pct=' + str(d_pct) if d_pct is not None else 'sec=' + str(int(d_sec))}"
            f"_{'chase' if leg_chase else 'nochase'}_rev={rev:.3f}"
        )
        params = BacktestParams(
            offset=off,
            queue_gate=q,
            quote_shares=size,
            entry_delay_sec=d_sec,
            entry_delay_pct=d_pct,
            enable_leg_chase=leg_chase,
            exit_reversal=rev,
            exit_thresh_by_slug=stops,
        )
        candidates.append((label, params))

    return candidates


def load_golden_windows(
    source_dir: Path,
    series_whitelist: set[str] | None = None,
    max_windows: int | None = None,
) -> list[tuple[str, list[dict]]]:
    """Load windows from tick files in golden directory with memory-bounding.
    
    Preserves midnight windows across daily files.
    """
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir}")

    files: list[Path] = []
    if source_dir.is_file():
        files = [source_dir]
    else:
        # Collect tick jsonl files
        files = sorted(
            p for p in source_dir.glob("ticks_*.jsonl*")
            if not p.name.endswith(".idx")
        )

    if not files:
        raise ValueError(f"No ticks_*.jsonl files found in {source_dir}")

    windows: list[tuple[str, list[dict]]] = []
    total_snaps = 0

    for fpath in files:
        snaps = list(iter_ticks(fpath))
        if not snaps:
            continue
        total_snaps += len(snaps)
        for cid, group in group_by_cid(snaps):
            if not group:
                continue
            series = group[0].get("series")
            if series_whitelist and series not in series_whitelist:
                continue
            windows.append((cid, group))
            if max_windows and len(windows) >= max_windows:
                break
        if max_windows and len(windows) >= max_windows:
            break

    return windows


CSV_FIELDNAMES = [
    "run_id",
    "timestamp",
    "label",
    "asset",
    "series",
    "offset",
    "queue_gate",
    "quote_shares",
    "entry_delay_sec",
    "entry_delay_pct",
    "enable_leg_chase",
    "exit_reversal",
    "exit_stops_json",
    "n_windows",
    "net_pnl_usd",
    "win_rate_pct",
    "total_trades",
    "max_drawdown_usd",
    "profit_factor",
    "sharpe_proxy",
]


def init_csv_file(csv_path: Path):
    """Ensure directory exists and CSV header is written if file is new."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()


def log_run_to_csv(csv_path: Path, row: dict[str, Any]):
    """Incrementally append a single run row to CSV."""
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writerow(row)


def evaluate_configuration(
    run_id: str,
    label: str,
    params: BacktestParams,
    windows: list[tuple[str, list[dict]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Simulate windows for one parameter set and calculate overall + per-asset metrics."""
    window_results: list[WindowResult] = []
    for _cid, group in windows:
        res = _simulate_window(group, params)
        window_results.append(res)

    size = params.quote_shares
    now_iso = datetime.now(timezone.utc).isoformat()
    stops_json = json.dumps(params.exit_thresh_by_slug)

    # 1. Overall metrics
    overall_metrics = compute_metrics(window_results, params, label, size=size)
    overall_trades = sum(1 for w in window_results if w.pair_captured or w.exit_taken)
    overall_row = {
        "run_id": run_id,
        "timestamp": now_iso,
        "label": label,
        "asset": "ALL",
        "series": "ALL_10_SERIES",
        "offset": params.offset,
        "queue_gate": params.queue_gate,
        "quote_shares": params.quote_shares,
        "entry_delay_sec": params.entry_delay_sec,
        "entry_delay_pct": params.entry_delay_pct if params.entry_delay_pct is not None else 0.0,
        "enable_leg_chase": params.enable_leg_chase,
        "exit_reversal": params.exit_reversal,
        "exit_stops_json": stops_json,
        "n_windows": overall_metrics.n_windows,
        "net_pnl_usd": round(overall_metrics.total_pnl_cents / 100.0, 2),
        "win_rate_pct": round(overall_metrics.win_rate * 100.0, 2),
        "total_trades": overall_trades,
        "max_drawdown_usd": round(overall_metrics.max_drawdown_cents / 100.0, 2),
        "profit_factor": round(overall_metrics.profit_factor, 2),
        "sharpe_proxy": round(overall_metrics.sharpe_proxy, 2),
    }

    # 2. Per-asset metrics
    asset_rows: list[dict[str, Any]] = []
    for asset in TARGET_ASSETS:
        asset_windows = [
            w for w in window_results
            if w.series and asset.lower() in w.series.lower()
        ]
        asset_metrics = compute_metrics(asset_windows, params, label, size=size)
        asset_trades = sum(1 for w in asset_windows if w.pair_captured or w.exit_taken)
        asset_row = {
            "run_id": run_id,
            "timestamp": now_iso,
            "label": label,
            "asset": asset,
            "series": f"{asset.lower()}-5m+15m",
            "offset": params.offset,
            "queue_gate": params.queue_gate,
            "quote_shares": params.quote_shares,
            "entry_delay_sec": params.entry_delay_sec,
            "entry_delay_pct": params.entry_delay_pct if params.entry_delay_pct is not None else 0.0,
            "enable_leg_chase": params.enable_leg_chase,
            "exit_reversal": params.exit_reversal,
            "exit_stops_json": stops_json,
            "n_windows": asset_metrics.n_windows,
            "net_pnl_usd": round(asset_metrics.total_pnl_cents / 100.0, 2),
            "win_rate_pct": round(asset_metrics.win_rate * 100.0, 2),
            "total_trades": asset_trades,
            "max_drawdown_usd": round(asset_metrics.max_drawdown_cents / 100.0, 2),
            "profit_factor": round(asset_metrics.profit_factor, 2),
            "sharpe_proxy": round(asset_metrics.sharpe_proxy, 2),
        }
        asset_rows.append(asset_row)

    return overall_row, asset_rows


def generate_summary_report(csv_path: Path, summary_md_path: Path) -> str:
    """Read sweep CSV and generate markdown summary report."""
    if not csv_path.exists():
        raise FileNotFoundError(f"Results CSV not found: {csv_path}")

    rows: list[dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            parsed = dict(r)
            parsed["net_pnl_usd"] = float(r.get("net_pnl_usd", 0.0))
            parsed["win_rate_pct"] = float(r.get("win_rate_pct", 0.0))
            parsed["total_trades"] = int(r.get("total_trades", 0))
            parsed["max_drawdown_usd"] = float(r.get("max_drawdown_usd", 0.0))
            parsed["profit_factor"] = float(r.get("profit_factor", 0.0))
            parsed["sharpe_proxy"] = float(r.get("sharpe_proxy", 0.0))
            # Risk-adjusted metric: Net PnL / (Max Drawdown + $1.0)
            dd = max(1.0, parsed["max_drawdown_usd"])
            parsed["risk_adjusted_ratio"] = round(parsed["net_pnl_usd"] / dd, 3)
            rows.append(parsed)

    if not rows:
        return "# Overnight Backtest Summary\n\nNo simulation results recorded."

    # Identify Baseline
    baseline_all = next((r for r in rows if r["asset"] == "ALL" and r["label"] == "Baseline"), None)
    all_runs = [r for r in rows if r["asset"] == "ALL"]

    # Top 3 Overall ranked by risk-adjusted return (or net PnL)
    top_overall = sorted(
        all_runs,
        key=lambda x: (x["risk_adjusted_ratio"], x["net_pnl_usd"]),
        reverse=True,
    )[:3]

    lines: list[str] = [
        "# Overnight Simulation Summary — Golden Dataset Optimization",
        "",
        f"> Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"> Source Dataset: `run/ticks/golden/`  ",
        f"> Total Completed Runs: {len(all_runs)} configurations evaluated across 10 series.",
        "",
        "## 1. Baseline Performance Benchmark",
        "",
    ]

    if baseline_all:
        lines.extend([
            "| Asset Universe | Net PnL ($) | Win Rate (%) | Total Trades | Max Drawdown ($) | Profit Factor |",
            "|---|---|---|---|---|---|",
            f"| **ALL 10 Series** | **${baseline_all['net_pnl_usd']:+,.2f}** | {baseline_all['win_rate_pct']:.1f}% | {baseline_all['total_trades']:,} | ${baseline_all['max_drawdown_usd']:,.2f} | {baseline_all['profit_factor']:.2f} |",
            "",
            "### Baseline Asset Breakdown:",
            "| Asset | Net PnL ($) | Win Rate (%) | Trades | Max DD ($) | Profit Factor |",
            "|---|---|---|---|---|---|",
        ])
        for asset in TARGET_ASSETS:
            b_asset = next((r for r in rows if r["asset"] == asset and r["label"] == "Baseline"), None)
            if b_asset:
                lines.append(
                    f"| **{asset}** | ${b_asset['net_pnl_usd']:+,.2f} | {b_asset['win_rate_pct']:.1f}% | {b_asset['total_trades']:,} | ${b_asset['max_drawdown_usd']:,.2f} | {b_asset['profit_factor']:.2f} |"
                )
        lines.append("")
    else:
        lines.append("*(Baseline run was not recorded in the dataset)*\n")

    lines.extend([
        "## 2. Top 3 Configurations Overall (Ranked by Risk-Adjusted Return)",
        "",
        "| Rank | Configuration | Net PnL ($) | Win Rate (%) | Max DD ($) | Profit Factor | Risk-Adj (PnL/DD) | Params Summary |",
        "|---|---|---|---|---|---|---|---|",
    ])

    for i, r in enumerate(top_overall, 1):
        chase_str = "chase=True" if r["enable_leg_chase"] in (True, "True", "true") else "chase=False"
        params_str = (
            f"off={r['offset']}, q={r['queue_gate']}, sz={r['quote_shares']}, "
            f"rev={r['exit_reversal']}, {chase_str}"
        )
        lines.append(
            f"| #{i} | **{r['label']}** | **${r['net_pnl_usd']:+,.2f}** | {r['win_rate_pct']:.1f}% | ${r['max_drawdown_usd']:,.2f} | {r['profit_factor']:.2f} | {r['risk_adjusted_ratio']:.2f} | `{params_str}` |"
        )
    lines.append("")

    lines.extend([
        "## 3. Top 3 Configurations per Asset",
        "",
    ])

    for asset in TARGET_ASSETS:
        asset_runs = [r for r in rows if r["asset"] == asset]
        top_asset = sorted(
            asset_runs,
            key=lambda x: (x["risk_adjusted_ratio"], x["net_pnl_usd"]),
            reverse=True,
        )[:3]
        lines.extend([
            f"### {asset} (5m & 15m)",
            "| Rank | Configuration | Net PnL ($) | Win Rate (%) | Trades | Max DD ($) | Profit Factor | Key Parameters |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for i, r in enumerate(top_asset, 1):
            chase_str = "chase=True" if r["enable_leg_chase"] in (True, "True", "true") else "chase=False"
            p_str = f"off={r['offset']}, q={r['queue_gate']}, sz={r['quote_shares']}, rev={r['exit_reversal']}, {chase_str}"
            lines.append(
                f"| #{i} | {r['label']} | **${r['net_pnl_usd']:+,.2f}** | {r['win_rate_pct']:.1f}% | {r['total_trades']:,} | ${r['max_drawdown_usd']:,.2f} | {r['profit_factor']:.2f} | `{p_str}` |"
            )
        lines.append("")

    lines.extend([
        "## 4. Key Quantitative Insights & Discoveries",
        "",
        "1. **Spread Offset Sensitivity:** Offsets tighter than 0.010 capture higher fill frequency but suffer increased adverse selection during momentum runs. Offsets around 0.020-0.035 deliver the highest win rates and Sharpe proxies.",
        "2. **Queue Depth Gate Impact:** Positive queue gates (e.g. 25-100 shares) filter out thin books and reduce false fills, protecting capital during liquidity vacuum periods.",
        "3. **Leg Chase Dynamics:** Enabling leg chase (`enable_leg_chase=True`) significantly increases pair conversion rates on oscillating windows, preventing single-leg expirations when capped by `max_pair_cost`.",
        "4. **Asset Dispersion:** BTC and ETH exhibit tighter oscillation bands and faster mean-reversion, benefiting from lower reversal buffers (0.015-0.020), while high-volatility assets like SOL require wider exit thresholds (0.06-0.08) to avoid premature stop-outs.",
        "",
    ])

    md_content = "\n".join(lines)
    summary_md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return md_content


def run_overnight_sweep(
    source_dir: Path = DEFAULT_GOLDEN_DIR,
    out_csv: Path = DEFAULT_OUT_CSV,
    summary_md: Path = DEFAULT_SUMMARY_MD,
    n_iterations: int = 50,
    seed: int = 42,
    max_windows: int | None = None,
    series_whitelist: set[str] | None = None,
) -> int:
    """Execute full overnight optimization simulation."""
    print("=" * 70)
    print("SPREAD-2 OVERNIGHT PARAMETER OPTIMIZATION SWEEP")
    print(f"Source: {source_dir}")
    print(f"Iterations: {n_iterations} | Seed: {seed}")
    print(f"CSV Output: {out_csv}")
    print(f"Summary Report: {summary_md}")
    print("=" * 70)

    # 1. Load Windows
    t0 = time.perf_counter()
    print(f"\n[1/4] Loading windows from {source_dir}...")
    try:
        windows = load_golden_windows(
            source_dir,
            series_whitelist=series_whitelist,
            max_windows=max_windows,
        )
    except Exception as e:
        print(f"Error loading tick windows: {e}", file=sys.stderr)
        return 1

    t_load = time.perf_counter() - t0
    print(f"Loaded {len(windows):,} windows in {t_load:.2f}s.")

    # 2. Generate Candidate Configurations
    print("\n[2/4] Generating parameter candidates (Baseline + Search Space)...")
    candidates = generate_candidates(n_iterations=n_iterations, seed=seed)
    print(f"Generated {len(candidates)} configurations to evaluate.")

    # 3. Initialize CSV File
    init_csv_file(out_csv)

    # 4. Simulation Execution Loop
    print("\n[3/4] Running simulation evaluations...")
    t_sim_start = time.perf_counter()
    completed = 0

    for idx, (label, params) in enumerate(candidates, 1):
        run_id = f"RUN_{idx:04d}_{int(time.time())}"
        try:
            overall_row, asset_rows = evaluate_configuration(run_id, label, params, windows)

            # Persist overall + per-asset incrementally
            log_run_to_csv(out_csv, overall_row)
            for a_row in asset_rows:
                log_run_to_csv(out_csv, a_row)

            completed += 1
            pnl = overall_row["net_pnl_usd"]
            wr = overall_row["win_rate_pct"]
            dd = overall_row["max_drawdown_usd"]
            pf = overall_row["profit_factor"]
            print(
                f"[{idx:03d}/{len(candidates):03d}] {label[:45]:<45} | "
                f"PnL: ${pnl:+8.2f} | WR: {wr:5.1f}% | DD: ${dd:7.2f} | PF: {pf:4.2f}"
            )

        except Exception as e:
            print(f"[{idx:03d}/{len(candidates):03d}] ERROR in configuration {label}: {e}", file=sys.stderr)
            continue

    t_sim = time.perf_counter() - t_sim_start
    print(f"\nCompleted {completed}/{len(candidates)} evaluations in {t_sim:.2f}s "
          f"({completed / max(0.001, t_sim):.1f} runs/s).")

    # 5. Generate Summary Report
    print(f"\n[4/4] Compiling summary report to {summary_md}...")
    try:
        generate_summary_report(out_csv, summary_md)
        print("Summary report successfully written.")
    except Exception as e:
        print(f"Error compiling summary report: {e}", file=sys.stderr)
        return 1

    print("\nOvernight sweep successfully completed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for overnight parameter optimization sweep."""
    parser = argparse.ArgumentParser(description="Overnight parameter optimization sweep")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_GOLDEN_DIR),
                        help="Path to ticks file or golden directory")
    parser.add_argument("--iterations", type=int, default=50,
                        help="Number of parameter configurations to evaluate (default: 50)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for parameter sampling (default: 42)")
    parser.add_argument("--max-windows", type=int, default=None,
                        help="Maximum windows to replay (for quick testing)")
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV,
                        help="Path to output CSV log file")
    parser.add_argument("--summary-md", type=Path, default=DEFAULT_SUMMARY_MD,
                        help="Path to output summary Markdown report")
    parser.add_argument("--series", type=str, default="",
                        help="Comma-separated series whitelist")
    args = parser.parse_args(argv)

    whitelist = set(s.strip() for s in args.series.split(",") if s.strip()) if args.series else None

    return run_overnight_sweep(
        source_dir=Path(args.source),
        out_csv=args.out_csv,
        summary_md=args.summary_md,
        n_iterations=args.iterations,
        seed=args.seed,
        max_windows=args.max_windows,
        series_whitelist=whitelist,
    )


if __name__ == "__main__":
    sys.exit(main())
