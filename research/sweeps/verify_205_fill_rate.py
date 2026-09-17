"""Verify issue #205's fill-rate measurement under the unified fill rule.

Issue #205 measured, over 550 gates-off windows, that the old ``fill_model="tape"``
filled 3.6% of windows while ``fill_model="cross"`` filled 57%. Issue #226 /
ADR-0002 removed the knob and unified both engines on ``book_math.resting_bid_filled``
(a print at our price OR the best ask fully through it) — closing the structural
question without ever re-running #205's numbers.

This script re-runs that measurement. It changes no engine code and no
``ev_lab``/``sim2`` code: it reuses the sweep lab's own ``build_cache`` +
``sim2`` pipeline, so the lab's guarded-knob policy is respected by
construction. ``sim2`` is the cached-window research simulator that already
implements the #226 unified fill rule (sell-filtered prints + ask-through)
and takes the gates that #205 disabled — ``entry_delay_sec=0``,
``quote_range=(0,1)`` — as its own call arguments, where the lab's guard
expects them. ``dead_zone_val`` and ``enable_leg_chase`` stay at their
defaults, which is the engine behaviour `sim2` mirrors (no dead-zone
hold, no chase), so the gates-off configuration needs no non-default value.

The two fill detectors are separated read-only, by wrapping
``book_math.resting_bid_filled`` (never editing it), so all three
configurations replay through one simulator:

- ``tape``  — detector (a) alone: blind the rule to the book.
- ``ask``   — detector (b) alone ("cross"): blind the rule to the tape.
- ``both``  — the unified rule, untouched.

Usage:
    python -m research.sweeps.verify_205_fill_rate [dataset.jsonl ...]

With no arguments, measures every ``run/ticks/ticks_*.jsonl`` via a fresh
``build_cache`` pass over those files (the cache is written to a scratch
path so the lab's own cache is never touched).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SWEEPS = REPO_ROOT / "research" / "sweeps"
for p in (str(REPO_ROOT), str(SWEEPS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ev_lab  # noqa: E402
import sim2 as sim2_mod  # noqa: E402
from backtest.engine import BacktestParams  # noqa: E402
from sim2 import sim2  # noqa: E402
from strategy import book_math  # noqa: E402


def gates_off_params(offset: float = 0.02) -> BacktestParams:
    """Params for the gates-off run: only `offset` departs from the default.

    Every entry gate #205 disabled is off by construction: `sim2` takes
    `entry_delay_sec=0` and `quote_range=(0.0, 1.0)` as call arguments (its
    own research extensions, where the lab's guard expects them), the dead
    zone and leg chase stay at their defaults (no hold, no chase — the
    behaviour the issue's gates-off configuration intended), and
    `queue_gate=0` disables the queue filter by default.
    """
    return BacktestParams(offset=offset)


def apply_detector(detector: str):
    """Restrict the shared fill rule to one of its two detectors, read-only.

    Yields the restore callable. Wrapping -- never editing -- the rule
    keeps `sim2`'s sell-print pre-filter intact; the wrap only narrows what
    the shared rule itself may see. `sim2` calls the rule through its own
    module-level name (`from strategy.book_math import resting_bid_filled`),
    so that binding is patched alongside the canonical one.
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
    sim2_mod.resting_bid_filled = wrapped
    def restore():
        book_math.resting_bid_filled = orig
        sim2_mod.resting_bid_filled = orig
    return restore


def build_cache_for(datasets: list[Path]) -> list[ev_lab.Win]:
    """Build the lab's window cache over exactly the given tick files.

    The cache is written to a scratch location and is reused when fresh
    (`build_cache` skips a rebuild when the file exists and `force=False`),
    so repeated detector runs in one session parse the day only once.
    """
    ticks_dir = datasets[0].parent
    old_ticks, old_cache = ev_lab.TICKS_DIR, ev_lab.CACHE_PATH
    ev_lab.TICKS_DIR = ticks_dir
    scratch = Path(ticks_dir) / ".verify_205_cache"
    scratch.mkdir(exist_ok=True)
    ev_lab.CACHE_PATH = scratch / "window_cache.pkl"
    try:
        ev_lab.build_cache(force=False)
        return ev_lab.load_cache()
    finally:
        ev_lab.TICKS_DIR, ev_lab.CACHE_PATH = old_ticks, old_cache


def run_measurement(windows: list[ev_lab.Win], params: BacktestParams,
                    detector: str = "both") -> Counter:
    """Sim every window through `sim2` under one detector, count fill outcomes.

    The 550-window run is ~10s on the built cache, so no progress printing.
    """
    restore = apply_detector(detector)
    c: Counter = Counter()
    try:
        for w in windows:
            r = sim2(w, params)
            fu, fd = bool(r["filled_up"]), bool(r["filled_dn"])
            if fu:
                c["up"] += 1
            if fd:
                c["down"] += 1
            if fu and fd:
                c["both"] += 1
            if fu or fd:
                c["any_leg"] += 1
            if r["pair"]:
                c["pairs"] += 1
            c["windows"] += 1
    finally:
        restore()
    return c


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

    # One cache build for every detector run in this invocation; the scratch
    # cache is reused across runs (`build_cache` skips when fresh).
    all_windows = build_cache_for(datasets)
    by_day: dict[str, list] = {}
    for w in all_windows:
        by_day.setdefault(w.day, []).append(w)

    for ds in datasets:
        day = ds.stem.replace("ticks_", "")
        wins = by_day.get(day, [])
        print(f"== {ds.name}  detector={detector}")
        print(f"{'offset':>7} {'windows':>7} {'any_leg':>7} {'up':>5} {'down':>5} "
              f"{'both':>5} {'pairs':>6} {'any_%':>6}")
        for off in offsets:
            counts = run_measurement(wins, gates_off_params(off), detector)
            pct = (counts["any_leg"] / counts["windows"] * 100.0) if counts["windows"] else 0.0
            print(f"{off:>7.2f} {counts['windows']:>7} {counts['any_leg']:>7} "
                  f"{counts['up']:>5} {counts['down']:>5} {counts['both']:>5} "
                  f"{counts['pairs']:>6} {pct:>5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
