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
from dataclasses import asdict, dataclass, replace
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
    # Drift-skip re-entry (issue #95) telemetry: windows the re-entry rule
    # recovered after the adverse-open gate skipped them, and their net PnL.
    reentered_windows: int
    reentered_pnl_cents: float
    # Issue #455: windows with at least one filled leg, counted once — the
    # unit of the sample gate (includes settlement-only legs).
    filled_windows: int = 0

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
            reentered_windows=0,
            reentered_pnl_cents=0.0,
            filled_windows=0,
        )

    pairs = sum(1 for w in window_results if w.pair_captured)
    exits = sum(1 for w in window_results if w.exit_taken)
    filled_windows = sum(1 for w in window_results if w.filled_up or w.filled_down)
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

    # Drift-skip re-entry (issue #95) telemetry: how many windows the re-entry
    # rule recovered and what they contributed net of fees, scaled by size.
    reentered = [w for w in window_results if w.reentry_count > 0]
    reentered_windows = len(reentered)
    reentered_pnl_cents = sum(
        (w.pnl_cents - w.fees_cents) * size for w in reentered
    )

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
        reentered_windows=reentered_windows,
        reentered_pnl_cents=round(reentered_pnl_cents, 2),
        filled_windows=filled_windows,
    )


# Issue #455: a configuration with fewer filled windows than this never
# becomes an incumbent or a confirmed winner, no matter its PnL.
MIN_FILLED_WINDOWS = 30


def passes_sample_gate(result: SweepRunResult, min_filled_windows: int = MIN_FILLED_WINDOWS) -> bool:
    """True only when the result rests on a real trade sample."""
    return result.filled_windows >= min_filled_windows


def split_windows_chronologically(
    grouped_windows: list[tuple[str, list[dict]]],
    holdout_frac: float,
) -> tuple[list[tuple[str, list[dict]]], list[tuple[str, list[dict]]], dict]:
    """Split whole CID groups into in-sample and holdout partitions.

    The boundary T is the start_ts at floor(n * (1 - holdout_frac)) over
    start_ts-sorted clocked windows. In-sample takes end_ts <= T, holdout
    takes start_ts >= T; crossing windows are purged, unclocked ones counted.
    """
    clocked: list[tuple[float, float, tuple[str, list[dict]]]] = []
    unclocked = 0
    for group in grouped_windows:
        first = group[1][0] if len(group) > 1 and group[1] else {}
        try:
            start_ts = float(first.get("start_ts"))
            end_ts = float(first.get("end_ts"))
        except (TypeError, ValueError):
            unclocked += 1
            continue
        if not (math.isfinite(start_ts) and math.isfinite(end_ts) and end_ts > start_ts):
            unclocked += 1
            continue
        clocked.append((start_ts, end_ts, group))
    clocked.sort(key=lambda item: item[0])
    n = len(clocked)
    boundary = clocked[math.floor(n * (1.0 - holdout_frac))][0] if n else 0.0
    in_sample: list[tuple[str, list[dict]]] = []
    holdout: list[tuple[str, list[dict]]] = []
    purged = 0
    for start_ts, end_ts, group in clocked:
        if end_ts <= boundary:
            in_sample.append(group)
        elif start_ts >= boundary:
            holdout.append(group)
        else:
            purged += 1
    summary = {
        "in_sample": len(in_sample),
        "holdout": len(holdout),
        "purged": purged,
        "unclocked": unclocked,
    }
    return in_sample, holdout, summary


def run_coordinate_descent(
    in_sample_windows: list[tuple[str, list[dict]]],
    base_params,
    size: int = 5,
    series_whitelist: set[str] | None = None,
    max_start_delay_sec: float = 0.0,
    include_structural: bool = False,
    min_filled_windows: int = MIN_FILLED_WINDOWS,
    max_passes: int = 5,
) -> tuple:
    """Best-improvement coordinate descent from a baseline, gate-aware.

    Each pass evaluates the 1D sensitivity neighborhood of the incumbent via
    run_sweep. Gated-out candidates can never win; the incumbent below the
    gate scores -infinity. Returns (incumbent_params, incumbent_result,
    baseline_result, history).
    """
    def _key(params) -> str:
        """Stable identity for a params object, so no candidate is evaluated twice."""
        return json.dumps(asdict(params), sort_keys=True, default=str)

    def _score(result: SweepRunResult) -> float:
        """Gate-aware score: a candidate below the sample gate scores -infinity."""
        if not passes_sample_gate(result, min_filled_windows):
            return float("-inf")
        return result.total_pnl_cents

    baseline_result = run_sweep(
        in_sample_windows, [("Baseline", base_params)],
        series_whitelist=series_whitelist, size=size,
        max_start_delay_sec=max_start_delay_sec)[0]
    incumbent_params = base_params
    incumbent_result = baseline_result
    incumbent_score = _score(baseline_result)
    seen = {_key(base_params)}
    history: list[dict] = []
    accepted_runs: list[dict] = [baseline_result.to_dict()]
    for pass_no in range(1, max_passes + 1):
        grid = deduplicate_grid(generate_sensitivity_grid(
            base_params=incumbent_params, size=size,
            include_structural=include_structural))
        fresh = [(label, params) for label, params in grid if _key(params) not in seen]
        for _, params in fresh:
            seen.add(_key(params))
        if not fresh:
            break
        results = run_sweep(
            in_sample_windows, fresh,
            series_whitelist=series_whitelist, size=size,
            max_start_delay_sec=max_start_delay_sec)
        gate_rejected = sum(
            1 for r in results if not passes_sample_gate(r, min_filled_windows))
        gated = [r for r in results if passes_sample_gate(r, min_filled_windows)]
        best = max(gated, key=lambda r: r.total_pnl_cents) if gated else None
        if best is not None and best.total_pnl_cents > incumbent_score:
            incumbent_params = best.params
            incumbent_result = best
            incumbent_score = best.total_pnl_cents
            accepted_runs.append(best.to_dict())
            history.append({
                "pass": pass_no,
                "accepted": f"p{pass_no}: {best.param_label}",
                "total_pnl_cents": best.total_pnl_cents,
                "filled_windows": best.filled_windows,
                "gate_rejected": gate_rejected,
            })
        else:
            history.append({
                "pass": pass_no,
                "accepted": None,
                "total_pnl_cents": incumbent_result.total_pnl_cents,
                "filled_windows": incumbent_result.filled_windows,
                "gate_rejected": gate_rejected,
            })
            break
    return incumbent_params, incumbent_result, baseline_result, history, accepted_runs


def confirm_on_holdout(
    holdout_windows: list[tuple[str, list[dict]]],
    baseline_params,
    winner_params,
    size: int = 5,
    series_whitelist: set[str] | None = None,
    max_start_delay_sec: float = 0.0,
    min_filled_windows: int = MIN_FILLED_WINDOWS,
) -> tuple:
    """Confirm the in-sample winner against the baseline on holdout data."""
    def _key(params) -> str:
        """Stable identity for a params object, so winner and baseline compare by value."""
        return json.dumps(asdict(params), sort_keys=True, default=str)

    results = run_sweep(
        holdout_windows, [("Baseline", baseline_params), ("Winner", winner_params)],
        series_whitelist=series_whitelist, size=size,
        max_start_delay_sec=max_start_delay_sec)
    baseline_result, winner_result = results[0], results[1]
    if _key(winner_params) == _key(baseline_params):
        return baseline_result, winner_result, False, "descent retained the baseline"
    if not passes_sample_gate(winner_result, min_filled_windows):
        return baseline_result, winner_result, False, "winner below sample gate on holdout"
    if not winner_result.total_pnl_cents > baseline_result.total_pnl_cents:
        return baseline_result, winner_result, False, "winner does not beat baseline on holdout"
    return baseline_result, winner_result, True, "winner beats baseline on gated holdout"


def _run_iterative(ap, args, grouped, whitelist, size, max_delay, include_structural) -> int:
    """Run the iterative preset: split, descend in-sample, confirm on holdout."""
    in_sample, holdout, split = split_windows_chronologically(grouped, args.holdout_frac)
    print(f"Split: {split['in_sample']} in-sample, {split['holdout']} holdout, "
          f"{split['purged']} purged, {split['unclocked']} unclocked.")
    if not in_sample or not holdout:
        ap.error("split left an empty partition (too few clocked windows)")
    base = BacktestParams(quote_shares=size)
    incumbent_params, incumbent_result, baseline_result, history, accepted_runs = \
        run_coordinate_descent(
            in_sample, base, size=size, series_whitelist=whitelist,
            max_start_delay_sec=max_delay, include_structural=include_structural,
            min_filled_windows=args.min_filled_windows, max_passes=args.max_passes)
    print("\n### Descent history (in-sample):")
    for entry in history:
        print(f"  pass {entry['pass']}: {entry['accepted']} "
              f"pnl={entry['total_pnl_cents']:.2f} filled={entry['filled_windows']} "
              f"gate_rejected={entry['gate_rejected']}")
    base_holdout, winner_holdout, confirmed, reason = confirm_on_holdout(
        holdout, base, incumbent_params, size=size, series_whitelist=whitelist,
        max_start_delay_sec=max_delay, min_filled_windows=args.min_filled_windows)
    print("\n### Holdout confirmation:")
    print(f"  baseline pnl={base_holdout.total_pnl_cents:.2f} "
          f"filled={base_holdout.filled_windows}")
    print(f"  winner   pnl={winner_holdout.total_pnl_cents:.2f} "
          f"filled={winner_holdout.filled_windows}")
    print(f"Confirmed: {confirmed} ({reason})")
    if args.out:
        out_payload = {
            "source": str(args.source),
            "preset": args.preset,
            "only": args.only,
            "include_structural": include_structural,
            "size": size,
            "max_start_delay_sec": max_delay,
            "count": None,
            "seed": None,
            "min_filled_windows": args.min_filled_windows,
            "holdout_frac": args.holdout_frac,
            "max_passes": args.max_passes,
            "split": split,
            "history": history,
            "baseline_in_sample": baseline_result.to_dict(),
            "winner_in_sample": incumbent_result.to_dict(),
            "baseline_holdout": base_holdout.to_dict(),
            "winner_holdout": winner_holdout.to_dict(),
            "winner_params": asdict(incumbent_params),
            "confirmed": confirmed,
            "reason": reason,
            "n_runs": len(accepted_runs),
            "runs": accepted_runs,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out_payload, f, indent=2)
        print(f"\nWrote iterative sweep results to {args.out}")
    return 0


def generate_sensitivity_grid(
    base_params: BacktestParams | None = None,
    size: int = 5,
    include_structural: bool = False,
) -> list[tuple[str, BacktestParams]]:
    """Generate 1D sensitivity parameter variations against a fixed baseline.

    Issue #233: tuning knobs only by default. Structural limits (`max_pair_cost`,
    `quote_range`) are held at baseline unless `include_structural=True` (CLI:
    `--include-structural`); they are always reachable via `--only pair_cost` /
    `--only quote_range`, which is explicit operator intent on its own.
    """
    size = max(5, int(size))
    base = base_params or BacktestParams(quote_shares=size)
    grid: list[tuple[str, BacktestParams]] = []

    # 1. Baseline
    grid.append(("Baseline", base))

    # 2. Offset variations (1.0c to 4.0c)
    offsets = [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040]
    for off in offsets:
        if off != base.offset:
            p = replace(base, offset=off)
            grid.append((f"offset={off:.3f}", p))

    # 3. Queue gate variations
    queues = [0.0, 10.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0]
    for q in queues:
        if q != base.queue_gate:
            p = replace(base, queue_gate=q)
            grid.append((f"queue={q:.0f}", p))

    # 4. Exit threshold variations
    exit_5m_levels = [0.06, 0.08, 0.10, 0.12, 0.14, 0.16]
    for e in exit_5m_levels:
        ex_dict = dict(base.exit_thresh_by_slug)
        ex_dict["default_5m"] = e
        ex_dict["btc-up-or-down-5m"] = max(0.05, e - 0.03)
        ex_dict["sol-up-or-down-5m"] = max(0.06, e - 0.01)
        p = replace(base, exit_thresh_by_slug=ex_dict)
        grid.append((f"exit_5m={e:.2f}", p))

    # 5. Exit reversal variations (issue #110: 0.005 steps over 0.010-0.030)
    reversals = [0.010, 0.015, 0.020, 0.025, 0.030]
    for r in reversals:
        if r != base.exit_reversal:
            p = replace(base, exit_reversal=r)
            grid.append((f"exit_rev={r:.3f}", p))

    # 6. Pair cost gate variations — a STRUCTURAL limit (issue #233), so
    # only generated when the caller explicitly opts in. Issue #227 hard-caps
    # this at 1.00 (a binary pair settles there), so the old [1.01 .. 1.10]
    # sweep is five values the engine now refuses. Same five-point shape,
    # inside the legal range.
    pair_costs = [0.96, 0.97, 0.98, 0.99, 1.00]
    if include_structural:
        for pc in pair_costs:
            if pc != base.max_pair_cost:
                p = replace(base, max_pair_cost=pc)
                grid.append((f"pair_cost={pc:.2f}", p))

    # 7. Quote range (issue #228) — a STRUCTURAL limit (issue #233): only on
    # explicit opt-in. Varies the quotable mid bounds.
    if include_structural:
        quote_ranges = [
            (0.00, 1.00), (0.05, 0.95), (0.10, 0.90), (0.15, 0.85),
            (0.20, 0.80), (0.25, 0.75), (0.30, 0.70),
        ]
        for qr in quote_ranges:
            if qr != base.quote_range:
                p = replace(base, quote_range=qr)
                grid.append((f"quote_range={qr[0]:.2f}-{qr[1]:.2f}", p))

    # 8. Dead zone (issue #208) — a STRUCTURAL limit (issue #233, ADR-0003):
    # only on explicit opt-in. Two separate axes answer the two open questions
    # from docs/engine-decision-rules.md §8: how wide the untradeable tail
    # should be (pct axis), and whether the tail should be measured as a
    # fraction of the window or as absolute seconds (sec axis). The baseline
    # value (0.10 pct) is covered by the Baseline row itself; 0.0 on either
    # axis disables the guard and shows the cost of having none.
    if include_structural:
        dead_zone_pcts = [0.0, 0.05, 0.15, 0.20, 0.30]
        for dv in dead_zone_pcts:
            if dv != base.dead_zone_val or base.dead_zone_unit != "pct":
                p = replace(base, dead_zone_val=dv, dead_zone_unit="pct")
                grid.append((f"dead_zone_pct={dv:.3f}", p))
        # 30s is the 10% tail of a 5m window and 90s of a 15m window — the
        # two readings of the shipped default; 0.0 disables the guard.
        dead_zone_secs = [0.0, 15.0, 30.0, 60.0, 90.0, 120.0]
        for dv in dead_zone_secs:
            p = replace(base, dead_zone_val=dv, dead_zone_unit="sec")
            grid.append((f"dead_zone_sec={dv:.0f}", p))

    # 9. Entry delay (issues #145/#137) — TUNING knobs, swept by default. One
    # mixed axis: seconds rows clear `entry_delay_pct` (None) and percent rows
    # zero `entry_delay_sec`, because pct takes precedence when set
    # (`backtest/engine.py:139-141`). The off-point is the Baseline row.
    entry_delay_secs = [15.0, 30.0, 60.0]
    for dv in entry_delay_secs:
        if dv != base.entry_delay_sec or base.entry_delay_pct is not None:
            p = replace(base, entry_delay_sec=dv, entry_delay_pct=None)
            grid.append((f"entry_delay={dv:.0f}s", p))
    entry_delay_pcts = [0.10, 0.20]
    for dv in entry_delay_pcts:
        if dv != base.entry_delay_pct:
            p = replace(base, entry_delay_sec=0.0, entry_delay_pct=dv)
            grid.append((f"entry_delay={dv * 100:.0f}%", p))

    # 10. Leg chase (issue #164) — a TUNING knob, swept by default. The
    # off-point is the Baseline row itself when the base has it disabled.
    if base.enable_leg_chase is not True:
        grid.append(("leg_chase=on", replace(base, enable_leg_chase=True)))
    if base.enable_leg_chase is not False:
        grid.append(("leg_chase=off", replace(base, enable_leg_chase=False)))

    # 11. Naked leg at expiry (issue #229) — a STRUCTURAL limit (issue #233):
    # only on explicit opt-in, like pair_cost/quote_range/dead_zone. The gate
    # follows the registry (`param_class_for`), not a hard-coded list, so a
    # future reclassification flows through without touching this file.
    naked_leg_structural = (
        BacktestParams.param_class_for("naked_leg_at_expiry") == "structural"
    )
    if include_structural or not naked_leg_structural:
        for v in ("close", "hold"):
            if v != base.naked_leg_at_expiry:
                grid.append((f"naked_leg={v}", replace(base, naked_leg_at_expiry=v)))

    # 12. Late entry % (issue #348) — TUNING knob, swept by default.
    late_entry_pcts = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    for lp in late_entry_pcts:
        cur_pct = base.entry_delay_pct if base.entry_delay_pct is not None else 0.0
        if lp != cur_pct:
            p = replace(base, entry_delay_sec=0.0, entry_delay_pct=lp)
            grid.append((f"late_entry={lp * 100:.0f}%", p))

    return grid


#: Label prefixes of the 1D sensitivity axes; the `--only` CLI flag keeps the
#: "Baseline" row plus rows whose label starts with one of these prefixes.
SENSITIVITY_AXES = (
    "offset", "queue", "exit_5m", "exit_rev", "pair_cost",
    "quote_range", "dead_zone_pct", "dead_zone_sec",
    "entry_delay", "leg_chase", "naked_leg", "late_entry",
)


def filter_sensitivity_grid(
    grid: list[tuple[str, BacktestParams]],
    only: str,
) -> list[tuple[str, BacktestParams]]:
    """Keep the Baseline row plus rows of a single 1D sensitivity axis.

    The value equal to the baseline is covered by the "Baseline" row itself
    (each axis loop skips it), so the filtered grid still spans the full axis.

    Issue #208: the compound axis "dead_zone" keeps the Baseline row plus
    BOTH dead-zone axes (pct and sec) in one run, so the pct-vs-sec question
    is answered from a single execution environment per dataset.
    """
    if only == "dead_zone":
        return [row for row in grid
                if row[0] == "Baseline"
                or row[0].startswith("dead_zone_pct=")
                or row[0].startswith("dead_zone_sec=")]
    prefix = only + "="
    return [row for row in grid if row[0] == "Baseline" or row[0].startswith(prefix)]


def deduplicate_grid(
    grid: list[tuple[str, BacktestParams]],
) -> list[tuple[str, BacktestParams]]:
    """Drop rows duplicating an earlier row's effective params (issue #348).

    The `entry_delay` pct rows and the `late_entry` rows can describe the
    same `BacktestParams` (sec=0.0 + pct=X); without dedup the default CLI
    replays those configurations twice. First label wins; `--only` paths
    are filtered before this runs so their complete axes are untouched.
    """
    unique: list[tuple[str, BacktestParams]] = []
    for row in grid:
        if any(row[1] == prev[1] for prev in unique):
            continue
        unique.append(row)
    return unique


def generate_joint_grid(
    offsets: Sequence[float] = (0.015, 0.020, 0.025, 0.030),
    queues: Sequence[float] = (0.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0),
    exit_5ms: Sequence[float] = (0.08, 0.10, 0.12, 0.14),
    exit_reversals: Sequence[float] = (0.015, 0.020),
    quote_ranges: Sequence[tuple[float, float]] | None = None,
    max_start_delay: float = 0.0,
    size: int = 5,
    include_structural: bool = False,
) -> list[tuple[str, BacktestParams]]:
    """Generate multi-dimensional Cartesian grid across tuning knobs.

    Issue #233: tuning knobs only by default — structural limits (`quote_range`,
    `max_pair_cost`) hold at the dataclass baseline. Pass `include_structural=True`
    (CLI: `--include-structural`) to sweep `quote_ranges` as well; `max_pair_cost`
    stays at the grid's historical 1.00 in that mode.

    `max_start_delay` is a dataset filter applied by `run_sweep`, not an
    engine parameter (issue #229 deleted `max_start_delay_sec`).
    """
    size = max(5, int(size))
    explicit_qr = quote_ranges is not None
    if quote_ranges is None:
        quote_ranges = ((0.10, 0.90),) if not include_structural else (
            (0.05, 0.95), (0.10, 0.90), (0.15, 0.85), (0.20, 0.80))
    grid: list[tuple[str, BacktestParams]] = []
    for off, q, e5, rev, qr in itertools.product(
            offsets, queues, exit_5ms, exit_reversals, quote_ranges):
        ex_dict = {
            "default_5m": e5,
            "default_15m": round(e5 + 0.01, 2),
            "btc-up-or-down-5m": max(0.05, round(e5 - 0.03, 2)),
            "sol-up-or-down-5m": max(0.06, round(e5 - 0.01, 2)),
            "btc-up-or-down-15m": round(e5 + 0.01, 2),
            "sol-up-or-down-15m": round(e5 + 0.01, 2),
        }
        label = f"off={off:.3f}_q={q:.0f}_ex={e5:.2f}_rev={rev:.3f}"
        if include_structural or explicit_qr:
            label += f"_qr={qr[0]:.2f}-{qr[1]:.2f}"
        p = BacktestParams(
            offset=off,
            queue_gate=q,
            max_pair_cost=1.00 if include_structural else 0.99,
            exit_thresh_by_slug=ex_dict,
            exit_reversal=rev,
            quote_shares=size,
            merge_gas_usd=0.0,
            taker_fee_rate=0.07,
            quote_range=qr,
        )
        grid.append((label, p))
    return grid


def generate_random_grid(
    count: int = 50,
    seed: int = 42,
    max_start_delay: float = 0.0,
    size: int = 5,
    include_structural: bool = False,
) -> list[tuple[str, BacktestParams]]:
    """Sample random parameter configurations from declared ranges with a deterministic seed.

    Issue #233: structural limits (`max_pair_cost`, `quote_range`) hold at the
    dataclass baseline unless `include_structural=True` (CLI:
    `--include-structural`), in which case they vary as before.
    """
    rng = random.Random(seed)
    # Issue #307: a second stream for the new axes only. Legacy draws keep
    # their exact order, so a fixed seed replays the legacy prefix bit-for-bit.
    rng_new = random.Random(seed ^ 0x9E3779B9)
    size = max(5, int(size))
    offsets = [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040]
    queues = [0.0, 10.0, 25.0, 50.0, 100.0, 200.0]
    exit_5ms = [0.06, 0.08, 0.09, 0.10, 0.11, 0.12, 0.14, 0.16]
    exit_reversals = [0.010, 0.015, 0.020, 0.030]
    # (entry_delay_sec, entry_delay_pct) pairs; (0.0, None) is the off-point.
    entry_delays = [
        (0.0, None), (15.0, None), (30.0, None), (60.0, None),
        (0.0, 0.10), (0.0, 0.20),
    ]
    leg_chases = [False, True]
    naked_legs = ["close", "hold"] if include_structural else ["close"]
    # Structural axes (issue #233): only sampled when explicitly opted in.
    # Issue #227 hard-caps max_pair_cost at 1.00 (a binary pair settles there),
    # so the old [1.01 .. 1.10] sweep is five values the engine now refuses.
    pair_costs = [0.96, 0.97, 0.98, 0.99, 1.00]
    quote_ranges = [
        (0.00, 1.00), (0.05, 0.95), (0.10, 0.90), (0.15, 0.85),
        (0.20, 0.80), (0.25, 0.75), (0.30, 0.70),
    ]

    grid: list[tuple[str, BacktestParams]] = []
    seen: set[tuple] = set()
    for _ in range(count * 5):
        if len(grid) >= count:
            break
        off = rng.choice(offsets)
        q = rng.choice(queues)
        e5 = rng.choice(exit_5ms)
        rev = rng.choice(exit_reversals)
        if include_structural:
            pc = rng.choice(pair_costs)
            qr = rng.choice(quote_ranges)
        else:
            pc = 0.99
            qr = (0.10, 0.90)
        key = (off, q, e5, rev, pc, qr)
        if key in seen:
            continue
        seen.add(key)

        # New-axis draws happen only for accepted rows, on the separate
        # stream, so the legacy acceptance sequence never shifts.
        ed_sec, ed_pct = rng_new.choice(entry_delays)
        chase = rng_new.choice(leg_chases)
        naked = rng_new.choice(naked_legs)

        ex_dict = {
            "default_5m": e5,
            "default_15m": round(e5 + 0.01, 2),
            "btc-up-or-down-5m": max(0.05, round(e5 - 0.03, 2)),
            "sol-up-or-down-5m": max(0.06, round(e5 - 0.01, 2)),
            "btc-up-or-down-15m": round(e5 + 0.01, 2),
            "sol-up-or-down-15m": round(e5 + 0.01, 2),
        }
        label = f"rand_off={off:.3f}_q={q:.0f}_ex={e5:.2f}_rev={rev:.3f}"
        if include_structural:
            label += f"_qr={qr[0]:.2f}-{qr[1]:.2f}"
        # Sparse segments, only for non-baseline new-axis values, so a
        # baseline draw keeps the exact legacy label.
        if ed_pct is not None:
            label += f"_ed={ed_pct * 100:.0f}%"
        elif ed_sec != 0.0:
            label += f"_ed={ed_sec:.0f}s"
        if chase:
            label += "_chase=on"
        if naked != "close":
            label += "_naked=hold"
        p = BacktestParams(
            offset=off,
            queue_gate=q,
            max_pair_cost=pc,
            exit_thresh_by_slug=ex_dict,
            exit_reversal=rev,
            quote_shares=size,
            merge_gas_usd=0.0,
            taker_fee_rate=0.07,
            quote_range=qr,
            entry_delay_sec=ed_sec,
            entry_delay_pct=ed_pct,
            enable_leg_chase=chase,
            naked_leg_at_expiry=naked,
        )
        grid.append((label, p))
    return grid


def run_sweep(
    grouped_windows: list[tuple[str, list[dict]]],
    grid: list[tuple[str, BacktestParams]],
    series_whitelist: set[str] | None = None,
    size: int = 5,
    max_start_delay_sec: float = 0.0,
) -> list[SweepRunResult]:
    """Execute parameter sweep against pre-grouped condition windows.

    `max_start_delay_sec` filters the *dataset* (windows that started more
    than N seconds after their open are dropped before replay); it is not an
    engine parameter — issue #229 deleted that knob.
    """
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
            if max_start_delay_sec > 0:
                first_ts = float(snaps[0].get("ts", 0.0) or 0.0)
                start_ts = float(snaps[0].get("start_ts", 0.0) or 0.0)
                delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
                if delay > max_start_delay_sec:
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
        "| Rank | Configuration | PnL (cents) | Avg PnL | Win Rate | Pair Rate | Exit Rate | Re-Entry (#, PnL) | Max DD | Profit Factor | Sharpe |",
        "|:---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(sorted_res, 1):
        lines.append(
            f"| {i} | `{r.param_label}` | {r.total_pnl_cents:+.2f}c | "
            f"{r.avg_pnl_cents:+.2f}c | {r.win_rate * 100:.1f}% | "
            f"{r.pair_rate * 100:.1f}% | {r.exit_rate * 100:.1f}% | "
            f"{r.reentered_windows} ({r.reentered_pnl_cents:+.1f}c) | "
            f"{r.max_drawdown_cents:.2f}c | {r.profit_factor:.2f} | {r.sharpe_proxy:.2f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for backtest parameter sweep engine."""
    ap = argparse.ArgumentParser(description="SPREAD-2 Quant Parameter Sweep Runner")
    ap.add_argument("source", nargs="?", default=str(DEFAULT_TICKS),
                    help="ticks directory or .jsonl[.gz] file")
    ap.add_argument("--preset", choices=["sensitivity", "grid", "assets", "random", "iterative"], default="sensitivity",
                    help="Sweep preset: sensitivity (1D), grid (joint), assets (universe), random (stochastic), iterative (coordinate descent with sample gate)")
    ap.add_argument("--only", choices=list(SENSITIVITY_AXES) + ["dead_zone"], default=None,
                    help="Sensitivity preset only: run Baseline plus a single 1D axis "
                         "(e.g. --only exit_rev for the issue #110 mercy-distance sweep). "
                         "A structural axis (--only pair_cost / --only quote_range) is "
                         "explicit opt-in on its own; --only dead_zone keeps BOTH "
                         "dead-zone axes (pct and sec) in one run (issue #208).")
    ap.add_argument("--include-structural", action="store_true",
                    help="Issue #233: also sweep structural limits (max_pair_cost, "
                         "quote_range). Default presets sweep tuning knobs only.")
    ap.add_argument("--count", type=int, default=50,
                    help="Sample count for random sweep (default: 50)")
    ap.add_argument("--seed", type=int, default=42,
                    help="Deterministic seed for random sweep (default: 42)")
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
    ap.add_argument("--min-filled-windows", type=int, default=MIN_FILLED_WINDOWS,
                    help="Iterative preset only: configurations with fewer filled windows fail the 30-trade sample gate")
    ap.add_argument("--holdout-frac", type=float, default=0.3,
                    help="Iterative preset only: fraction of clocked windows held out for confirmation")
    ap.add_argument("--max-passes", type=int, default=5,
                    help="Iterative preset only: maximum coordinate-descent passes")
    args = ap.parse_args(argv)
    if args.only is not None and args.preset != "sensitivity":
        ap.error("--only requires --preset sensitivity")
    if args.preset == "iterative":
        if args.min_filled_windows < 1:
            ap.error("--min-filled-windows must be at least 1")
        if args.max_passes < 1:
            ap.error("--max-passes must be at least 1")
        if not 0.0 < args.holdout_frac < 1.0:
            ap.error("--holdout-frac must be strictly between 0 and 1")
    size = max(5, args.size)
    max_delay = args.max_start_delay
    if args.filter_partial and max_delay <= 0:
        max_delay = 5.0
    # Issue #233: naming a structural axis with --only IS explicit intent,
    # so it implies --include-structural for the sensitivity grid.
    include_structural = args.include_structural or args.only in (
        "pair_cost", "quote_range", "dead_zone_pct", "dead_zone_sec", "dead_zone",
        "naked_leg")

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

    if args.preset == "iterative":
        return _run_iterative(ap, args, grouped, whitelist, size, max_delay,
                              include_structural)

    # Build grid based on preset
    base = BacktestParams(quote_shares=size)

    if args.preset == "sensitivity":
        grid = generate_sensitivity_grid(base, size=size,
                                         include_structural=include_structural)
        if args.only is not None:
            grid = filter_sensitivity_grid(grid, args.only)
        else:
            grid = deduplicate_grid(grid)
    elif args.preset == "grid":
        grid = generate_joint_grid(max_start_delay=max_delay, size=size,
                                   include_structural=args.include_structural)
    elif args.preset == "random":
        grid = generate_random_grid(
            count=args.count,
            seed=args.seed,
            max_start_delay=max_delay,
            size=size,
            include_structural=args.include_structural,
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
            res = run_sweep(grouped, [(name, base)], series_whitelist=s_set, size=size,
                            max_start_delay_sec=max_delay)
            results.extend(res)
    else:
        results = run_sweep(grouped, grid, series_whitelist=whitelist, size=size,
                            max_start_delay_sec=max_delay)
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
            "only": args.only,
            "include_structural": args.include_structural,
            "size": size,
            "max_start_delay_sec": max_delay,
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
