"""Verify issue #205's fill-rate measurement under the unified fill rule.

Issue #205 measured, over 550 gates-off windows, that the old ``fill_model="tape"``
filled 3.6% of windows while ``fill_model="cross"`` filled 57%. Issue #226 /
ADR-0002 removed the knob and unified both engines on ``book_math.resting_bid_filled``
(a print at our price OR the best ask fully through it) — closing the structural
question without ever re-running #205's numbers.

This script re-runs that measurement. It changes no engine code: it constructs
gates-off ``BacktestParams`` (every entry gate disabled, dead zone disabled so the
retired ``max_start_elapsed_pct=1.0`` has a faithful stand-in), replays each
``run/ticks/ticks_*.jsonl`` dataset, and counts windows by fill outcome exactly as
the issue's table does: any-leg / up / down / both / pairs.

Gates-off mapping (CONSTRAINTS.md §6):
- ``entry_delay_sec=0.0``        — no patient-entry hold (matches the issue).
- ``quote_range=(0.0, 1.0)``     — widest quotable range; disables the #228 band,
                                   the successor of the issue's ``entry_band=0``.
- ``queue_gate=0.0``             — queue depth filter off (matches the issue).
- ``dead_zone_val=0.0``          — disables the dead zone entirely. This is the
                                   stand-in for the issue's
                                   ``max_start_elapsed_pct=1.0``: with no dead zone
                                   a window may be entered on any tick, which is
                                   the "enter everything, always" the issue's
                                   gates-off configuration intended.
- ``enable_leg_chase=False``     — the issue ran with the chase off.

Usage:
    python -m research.sweeps.verify_205_fill_rate [dataset.jsonl ...]

With no arguments, replays every ``run/ticks/ticks_*.jsonl``.
Output: one summary row per dataset, printed as a table.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backtest.engine import BacktestParams, iter_ticks, replay  # noqa: E402
from strategy import book_math  # noqa: E402


def gates_off_params(offset: float = 0.02) -> BacktestParams:
    """Gates-off params matching issue #205's configuration (see module docstring)."""
    return BacktestParams(
        offset=offset,
        queue_gate=0.0,
        entry_delay_sec=0.0,
        quote_range=(0.0, 1.0),
        dead_zone_val=0.0,
        enable_leg_chase=False,
    )


def count_fills(result: dict) -> Counter:
    """Count windows by fill outcome, mirroring issue #205's table."""
    c: Counter = Counter()
    for w in result["per_window"]:
        fu, fd = bool(w["filled_up"]), bool(w["filled_down"])
        if fu:
            c["up"] += 1
        if fd:
            c["down"] += 1
        if fu and fd:
            c["both"] += 1
        if fu or fd:
            c["any_leg"] += 1
        c["pairs"] += int(w.get("pairs_count") or 0)
        c["windows"] += 1
    return c


def apply_detector(detector: str):
    """Restrict the shared fill rule to one of its two detectors, read-only.

    The unified rule fires on (a) a tape print at our price or (b) the best ask
    fully through it. #205's ``tape`` model was detector (a) alone; its
    ``cross`` model was detector (b) alone. Wrapping -- never editing --
    ``book_math.resting_bid_filled`` lets one engine replay all three
    configurations, so the tape-vs-cross gap is measured like-for-like.
    Yields the restore callable.
    """
    orig = book_math.resting_bid_filled
    if detector == "both":
        return lambda: None

    def wrapped(resting, best_ask, tape_prices, tick, newly_placed=False):
        if detector == "tape":
            # Detector (a) alone: blind the rule to the book.
            return orig(resting, None, tape_prices, tick, False)
        # Detector (b) alone ("cross"): blind the rule to the tape. The
        # marketable-arrival exception (`newly_placed`) is a book-side rule
        # and is kept, so a freshly placed marketable quote still fills.
        return orig(resting, best_ask, (), tick, newly_placed)

    book_math.resting_bid_filled = wrapped
    return lambda: setattr(book_math, "resting_bid_filled", orig)


def replay_dataset(path: Path, params: BacktestParams,
                   detector: str = "both") -> tuple[dict, Counter]:
    restore = apply_detector(detector)
    try:
        result = replay(iter_ticks(path), params)
    finally:
        restore()
    return result, count_fills(result)


def main(argv: list[str]) -> int:
    detector = "both"
    offsets = [0.02]
    paths: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--detector", "--offset") and i + 1 >= len(argv):
            print(f"{a} requires a value", file=sys.stderr)
            return 1
        if a == "--detector":
            detector = argv[i + 1]
            i += 2
            continue
        if a == "--offset":
            offsets = [float(argv[i + 1])]
            i += 2
            continue
        if a == "--offset-sweep":
            offsets = [0.01, 0.02, 0.05]
            i += 1
            continue
        paths.append(a)
        i += 1
    if detector not in ("tape", "ask", "both"):
        print(f"unknown detector {detector!r} (tape|ask|both)", file=sys.stderr)
        return 1
    if paths:
        datasets = [Path(p) for p in paths]
    else:
        ticks_dir = REPO_ROOT / "run" / "ticks"
        datasets = sorted(ticks_dir.glob("ticks_*.jsonl"))
    if not datasets:
        print("no datasets found (run/ticks/ticks_*.jsonl missing?)", file=sys.stderr)
        return 1

    for ds in datasets:
        print(f"== {ds.name}  detector={detector}")
        print(f"{'offset':>7} {'windows':>7} {'any_leg':>7} {'up':>5} {'down':>5} "
              f"{'both':>5} {'pairs':>6} {'any_%':>6}")
        for off in offsets:
            _, counts = replay_dataset(ds, gates_off_params(off), detector)
            pct = (counts["any_leg"] / counts["windows"] * 100.0) if counts["windows"] else 0.0
            print(f"{off:>7.2f} {counts['windows']:>7} {counts['any_leg']:>7} "
                  f"{counts['up']:>5} {counts['down']:>5} {counts['both']:>5} "
                  f"{counts['pairs']:>6} {pct:>5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
