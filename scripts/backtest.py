"""CLI wrapper for the backtest engine.

Reads ticks from a file or directory and prints per-series + overall stats.
Same parameters the dashboard sliders expose (Plan §2 / T3).

The configuration that `strategy/live_trader.py` runs
is reproducible here:

  python -m scripts.backtest run/ticks \
      --offset 0.03 --queue 0 --pair-cost 0.98 --size 5 \
      --entry-delay 60 --quote-lo 0.10 --quote-hi 0.90 \
      --dead-zone-val 0.10 --dead-zone-unit pct --naked-leg-at-expiry close \
      --exit-default-5m 0.49 --exit-default-15m 0.50 --max-start-delay 0

There is no fill model to choose (issue #226): one rule, the same one the live
engine runs.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable

from backtest import BacktestParams, group_by_cid, iter_ticks, replay

DEFAULT_TICKS = Path(__file__).resolve().parent.parent / "run" / "ticks"


def parse_exit_thresh(items: list[str]) -> dict:
    """Parse list of 'slug=thresh' CLI arguments into a dict."""
    out: dict = {}
    for it in items:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError:
            pass
    return out


def main(argv: list[str] | None = None):
    """Run SPREAD-2 backtest CLI replay on tick files and print performance summary."""
    ap = argparse.ArgumentParser(description="SPREAD-2 backtest replay")
    ap.add_argument("source", nargs="?", default=str(DEFAULT_TICKS),
                    help="ticks file, .jsonl.gz, or directory")
    ap.add_argument("--offset", type=float, default=0.020)
    ap.add_argument("--queue", type=float, default=50.0)
    ap.add_argument("--pair-cost", type=float, default=0.99)
    ap.add_argument("--exit", action="append", default=[],
                    help="key=value, e.g. --exit btc-up-or-down-5m=0.09 (repeatable)")
    ap.add_argument("--exit-default-5m", type=float, default=0.05)
    ap.add_argument("--exit-default-15m", type=float, default=0.05)
    ap.add_argument("--exit-reversal", type=float, default=0.02)
    ap.add_argument("--size", type=int, default=120)
    ap.add_argument("--gas", type=float, default=0.0)
    ap.add_argument("--max-start-delay", type=float, default=0.0,
                    help="filter late-started windows where first tick > N seconds after window open (0 disables)")
    ap.add_argument("--filter-partial", action="store_true",
                    help="shorthand to filter late-started partial windows (>5s delay)")
    # Issue #229: the dead zone owns the tail of the window — no entry timeout,
    # no late-start clock. `--filter-partial`/`--max-start-delay` survive as a
    # *dataset filter* only; they no longer touch engine parameters.
    ap.add_argument("--dead-zone-val", type=float, default=0.10,
                    help="tail of the window that is untradeable: fraction (pct) or seconds (sec); 0 disables")
    ap.add_argument("--dead-zone-unit", choices=("pct", "sec"), default="pct",
                    help="whether --dead-zone-val is a fraction of the window (pct) or absolute seconds (sec)")
    ap.add_argument("--naked-leg-at-expiry", choices=("close", "hold"), default="close",
                    help="unpaired leg in the dead zone: close at book bid (default) or hold to settlement")
    # The winning preset is defined by these two knobs as much as by --offset,
    # so without them the CLI could only replay a different strategy than the
    # one the bot runs, and the tape-vs-book comparison proved nothing about it.
    ap.add_argument("--entry-delay", type=float, default=0.0,
                    help="hold all quoting until N seconds into the window (0 disables)")
    ap.add_argument("--quote-lo", type=float, default=0.10,
                    help="lower bound of the quotable two-sided mid range (default 0.10)")
    ap.add_argument("--quote-hi", type=float, default=0.90,
                    help="upper bound of the quotable two-sided mid range (default 0.90)")
    ap.add_argument("--start", type=str, default=None,
                    help="inclusive start ISO timestamp, e.g. 2026-08-29T00:00:00Z")
    ap.add_argument("--end", type=str, default=None,
                    help="inclusive end ISO timestamp, e.g. 2026-08-30T23:59:59Z")
    ap.add_argument("--out", type=Path, default=None,
                    help="write full JSON to this path")
    args = ap.parse_args(argv)

    exit_thresh = parse_exit_thresh(args.exit)
    if "default_5m" not in exit_thresh:
        exit_thresh["default_5m"] = args.exit_default_5m
    if "default_15m" not in exit_thresh:
        exit_thresh["default_15m"] = args.exit_default_15m

    max_start_delay = args.max_start_delay
    if args.filter_partial and max_start_delay <= 0:
        max_start_delay = 5.0

    params = BacktestParams(
        offset=args.offset, queue_gate=args.queue,
        max_pair_cost=args.pair_cost, exit_thresh_by_slug=exit_thresh,
        exit_reversal=args.exit_reversal, quote_shares=args.size,
        merge_gas_usd=args.gas,
        dead_zone_val=args.dead_zone_val,
        dead_zone_unit=args.dead_zone_unit,
        naked_leg_at_expiry=args.naked_leg_at_expiry,
        entry_delay_sec=args.entry_delay,
        quote_range=(args.quote_lo, args.quote_hi),
    )
    print(f"source={args.source} offset={params.offset} queue={params.queue_gate} "
          f"pair_cost={params.max_pair_cost} "
          f"dead_zone={params.dead_zone_val}({params.dead_zone_unit}) "
          f"naked_leg_at_expiry={params.naked_leg_at_expiry} "
          f"filter_max_delay={max_start_delay}s "
          f"entry_delay={params.entry_delay_sec}s quote_range={params.quote_range} "
          f"params_hash={params.params_hash()}")

    t0 = time.perf_counter()
    snaps = list(iter_ticks(args.source))
    # Optional date-range filter (for midnight-crossing targeted replay)
    if args.start or args.end:
        from datetime import datetime, timezone
        def _to_ts(s: str) -> float:
            """Convert ISO string timestamp to Unix epoch seconds."""
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s).timestamp()
        start_ts = _to_ts(args.start) if args.start else 0.0
        end_ts = _to_ts(args.end) if args.end else float("inf")
        snaps = [s for s in snaps if start_ts <= s.get("ts", 0) <= end_ts]
    if max_start_delay > 0:
        filtered_snaps: list[dict] = []
        for _cid, group in group_by_cid(snaps):
            if not group:
                continue
            first_ts = float(group[0].get("ts", 0.0) or 0.0)
            start_ts_win = float(group[0].get("start_ts", 0.0) or 0.0)
            delay = max(0.0, first_ts - start_ts_win) if (first_ts and start_ts_win) else 0.0
            if delay <= max_start_delay:
                filtered_snaps.extend(group)
        snaps = filtered_snaps
    elapsed_load = time.perf_counter() - t0
    if not snaps:
        print("no ticks found", file=sys.stderr)
        return 1

    t1 = time.perf_counter()
    results = replay(snaps, params)
    elapsed_replay = time.perf_counter() - t1

    overall = results["aggregate"]["overall"]
    print(f"\nloaded {len(snaps)} snaps in {elapsed_load:.2f}s, "
          f"replayed {results['n_windows']} windows in {elapsed_replay:.2f}s")
    print(f"\nOverall ({overall['windows']} windows):")
    print(f"  pair_rate  {overall['pair_rate']*100:5.1f}%   "
          f"exit_rate {overall['exit_rate']*100:5.1f}%")
    print(f"  total_pnl  {overall['total_pnl_cents']:+8.2f}c   "
          f"avg_pnl  {overall['avg_pnl_cents']:+6.2f}c/win")
    print(f"  total_fees {overall['total_fees_cents']:8.2f}c")

    print("\nPer series:")
    for slug, a in sorted(results["aggregate"]["per_series"].items()):
        if a["windows"] == 0:
            continue
        print(f"  {slug:24s}  n={a['windows']:4d}  pair={a['pair_rate']*100:5.1f}%  "
              f"exit={a['exit_rate']*100:5.1f}%  "
              f"pnl={a['total_pnl_cents']:+8.2f}c  "
              f"avg={a['avg_pnl_cents']:+6.2f}c  "
              f"osc={a['oscillating']} mono={a['monotonic']}")

    if args.out:
        args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nfull results -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
