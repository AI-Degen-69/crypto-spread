"""Assemble the research cut of the golden dataset (issue #312).

The golden dataset (`run/ticks/golden/`) is the single canonical backtest set —
certified at 6 days / 4,910 windows / 1.43M ticks, 8–20x above the charter
floors. That headroom makes certification robust but makes every sweep pay full
volume: the measured full-set sweep costs ~100 minutes (716s load + 5,260s sim,
`docs/backtest-optimization-results.md` §0). The evolutionary iteration loop
(#311, OFAT sweeps) multiplies that cost dozens of times.

The **research cut** is the fix: a deterministic, stratified subset that keeps
every market-duration pair, every source day, and every window class at a
chosen multiple `M` (default 3) of the charter floors — RESEARCH_READY by
construction, replayed in a fraction of the time.

Rules (`docs/golden-tick-dataset.md`, §3.3, the backtest set):
- The golden dataset is READ-ONLY. Every golden day file's sha256 is verified
  unchanged after the build; the manifest records per-day source provenance.
- Cut lines are byte-identical copies of golden raw lines — replay results on
  the cut are identical to replay on the golden set, per window. The guardrail
  gate verifies this (per-window identity) before declaring success.
- Deterministic: same golden dir + same multiplier + same seed => byte-identical
  output. Final research claims still re-run on the full golden set.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.engine import _classify, _two_sided_mid  # noqa: E402
from backtest.index import build_index  # noqa: E402  (re-exported for tests)
from scripts.ship_to_drive import sha256_of  # noqa: E402
from strategy.series import SERIES  # noqa: E402

GOLDEN_MANIFEST_NAME = "golden_manifest.json"
MANIFEST_NAME = "research_manifest.json"
# M=3, not 4: the scarcest golden pair (bnb 15m) holds 159 windows, so M=4's
# 200-per-pair floor is infeasible on the certified set (159 < 200). M=3 needs
# >=150/pair (150 <= 159, feasible) and >=1,500 windows total.
DEFAULT_MULTIPLIER = 3
DEFAULT_SEED = 0

# Charter floors (docs/golden-tick-dataset.md §1.2) — the multiplier's base.
FLOOR_WINDOWS = 500
FLOOR_WINDOWS_PER_MARKET = 50


def _expected_market_pairs() -> set[tuple[str, int]]:
    """All 10 (series, duration) pairs every cut must represent."""
    return {(slug, duration) for slug, duration, _label in SERIES}


def _window_day_key(first_ts: float) -> str:
    """UTC day key of a window's first snap (attribution rule: day of first
    snap; straddling windows live whole in one cut file)."""
    import datetime as dt
    day = dt.datetime.fromtimestamp(first_ts, tz=dt.timezone.utc).date()
    return day.isoformat()


def _window_cells(snaps: list[dict]) -> dict[str, Any]:
    """Per-window metadata the stratifier needs: series, duration, day, class,
    mids (for classification only — never re-serialized)."""
    mids: list[float] = []
    for s in snaps:
        m = _two_sided_mid(s.get("up_book"), s.get("down_book"))
        if m is not None:
            mids.append(m)
    first = snaps[0]
    first_ts = float(first.get("ts") or 0.0)
    return {
        "series": first.get("series", ""),
        "duration": int(first.get("duration", 0)),
        "day": _window_day_key(first_ts),
        "class": _classify(mids),
        "n_snaps": len(snaps),
        "first_ts": first_ts,
    }


def _replay_parity_from_disk(windows: dict[str, dict[str, Any]],
                               out_dir: Path, selected: list[str],
                               params: Any) -> tuple[bool, int]:
    """Replay parity against the EMITTED bytes, not memory.

    Re-reads the cut day files from disk (`group_by_cid(iter_ticks(...))`)
    and replays every selected window from those bytes vs the golden-side
    in-memory groups. Comparing memory-to-memory would pass even with broken
    emission — this gate must read what was actually written.
    """
    from backtest.engine import _simulate_window, group_by_cid, iter_ticks

    cut_groups = dict(group_by_cid(list(iter_ticks(out_dir))))
    compared = 0
    for cid in selected:
        cut_snaps = cut_groups.get(cid)
        gold = windows.get(cid, {}).get("snaps")
        if not cut_snaps or not gold:
            return False, compared
        r_cut = _simulate_window(cut_snaps, params)
        r_gold = _simulate_window(gold, params)
        if (r_cut.pair_captured, r_cut.exit_taken, r_cut.pnl_cents,
                r_cut.filled_up, r_cut.filled_down, r_cut.class_label) != (
                r_gold.pair_captured, r_gold.exit_taken, r_gold.pnl_cents,
                r_gold.filled_up, r_gold.filled_down, r_gold.class_label):
            return False, compared
        compared += 1
    return True, compared


def _allocate(cells: dict[tuple, list[str]], target_total: int,
               floor_per_market: int) -> dict[tuple, int]:
    """Proportional allocation with two hard guarantees: every non-empty cell
    keeps >= 1 window, and every market-DURATION pair keeps >= floor_per_market
    windows (the same pair unit the verify policy counts).

    Cells are keyed (series, duration, day, class); a market is the leading
    (series, duration) pair. Floors are poured FIRST into each pair's own
    largest cells (capped by real cell sizes — never invented cells), then the
    remaining budget is spread proportionally by cell size, also capped. A pair
    whose total availability sits below the floor keeps everything it has and
    the guardrail fails loudly downstream (infeasible demand, not silent skew).
    """
    sizes = {key: len(v) for key, v in cells.items()}
    per_cell = {key: 1 for key in cells}
    pairs = sorted({key[:2] for key in cells})

    def pair_selected(pair: tuple) -> int:
        """Windows allocated so far to one (series, duration) pair."""
        return sum(n for k, n in per_cell.items() if k[:2] == pair)

    # Phase 1: floor guarantee per pair, split PROPORTIONALLY across that
    # pair's own cells (largest-remainder, capped by real cell sizes).
    # Largest-first pouring here would over-represent the biggest (day,
    # class) cells and break the "uniform within the finest cell" promise —
    # at M=3 the floors already sum to the target, so Phase 2 never runs and
    # Phase 1 IS the whole allocation.
    for pair in pairs:
        pcells = [k for k in cells if k[:2] == pair]
        avail = sum(sizes[k] for k in pcells)
        need = min(floor_per_market, avail) - pair_selected(pair)
        if need <= 0:
            continue
        psum = avail
        quotas: dict[tuple, list[int]] = {}
        for key in pcells:
            q, r = divmod(sizes[key] * need, psum)
            quotas[key] = [q, r]
        for key in pcells:
            take = min(quotas[key][0], sizes[key] - per_cell[key])
            per_cell[key] += take
            need -= take
        leftovers = sorted(pcells,
                           key=lambda k: (-quotas[k][1], -sizes[k]))
        for key in leftovers:
            if need <= 0:
                break
            if per_cell[key] < sizes[key]:
                per_cell[key] += 1
                need -= 1

    # Phase 2: spend the remaining budget proportionally by cell size, capped
    # by real availability; leftovers from flooring/caps go largest-first.
    budget = target_total - sum(per_cell.values())
    if budget > 0:
        size_sum = sum(sizes.values())
        order = sorted(cells, key=lambda k: -sizes[k])
        for key in order:
            if budget <= 0:
                break
            share = (sizes[key] * budget) // size_sum
            take = min(share, sizes[key] - per_cell[key], budget)
            if take <= 0:
                continue
            per_cell[key] += take
            budget -= take
        for key in order:
            if budget <= 0:
                break
            if per_cell[key] < sizes[key]:
                per_cell[key] += 1
                budget -= 1
    return per_cell


def build_research_cut(golden_dir: Path, out_dir: Path, *,
                       multiplier: int = DEFAULT_MULTIPLIER,
                       seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Build the research cut. Returns the manifest dict.

    Fails loudly (raises) without writing a complete-manifest claim when any
    guardrail fails: missing golden dir, floors not met, or replay parity.
    """
    golden_dir = Path(golden_dir)
    out_dir = Path(out_dir)
    if not golden_dir.is_dir():
        raise FileNotFoundError(f"golden dataset dir not found: {golden_dir}")
    # The golden set is read-only: never let --out point at it (the emission
    # cleanup below deletes *.jsonl/.idx/.json in out_dir).
    if (out_dir.resolve() == golden_dir.resolve()
            or golden_dir.resolve() in out_dir.resolve().parents):
        raise ValueError(f"--out {out_dir} must not be the golden dir or "
                         f"inside it (golden is read-only)")

    # --- Snapshot the golden set for the integrity guardrail ----------------
    golden_before = {p.name: sha256_of(p) for p in sorted(golden_dir.glob("*.jsonl"))}

    # Cross-check against the golden manifest itself: before/after equality
    # below only catches in-build writes, not pre-existing drift of a day
    # file away from its certified hash. Skipped when there is no manifest
    # (synthetic test fixtures) — the before/after check still applies.
    golden_manifest_path = golden_dir / GOLDEN_MANIFEST_NAME
    if golden_manifest_path.is_file():
        try:
            recorded = {d["file"]: d.get("sha256") or d.get("source_sha256")
                        for d in json.loads(
                            golden_manifest_path.read_text(encoding="utf-8")
                        ).get("days", [])}
        except (ValueError, KeyError, AttributeError) as e:
            raise ValueError(f"unreadable {golden_manifest_path}: {e}")
        drifted = [name for name, sha in golden_before.items()
                   if name in recorded and recorded[name] != sha]
        if drifted:
            raise ValueError(
                "golden drift: these day files no longer match "
                f"{GOLDEN_MANIFEST_NAME}: {sorted(drifted)}")

    # --- Group snaps into windows (pure, in-memory) -------------------------
    # Single ingestion pass: every raw line is parsed EXACTLY ONCE, and the
    # parsed snap keeps its original raw text ("_raw") so emission never
    # re-parses. (The previous implementation walked all files three times —
    # iter_ticks, then a raw-line pass with json.loads per line, then the
    # group — which dominated wall time on large golden sets.)
    grouped: dict[str, list[dict]] = {}
    skipped_lines = 0
    for p in sorted(golden_dir.glob("*.jsonl")):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    skipped_lines += 1  # malformed: same skip as iter_ticks
                    continue
                if not isinstance(obj, dict):
                    skipped_lines += 1
                    continue
                cid = obj.get("cid")
                if not cid:
                    skipped_lines += 1
                    continue
                # Keep the original raw text ON the parsed snap: emission must
                # copy bytes, not re-serialize, and must never re-parse.
                obj["_raw"] = line
                grouped.setdefault(str(cid), []).append(obj)
    grouped = {cid: snaps for cid, snaps in grouped.items() if snaps}
    windows: dict[str, dict[str, Any]] = {}
    for cid, snaps in grouped.items():
        snaps.sort(key=lambda s: float(s.get("ts") or 0.0))
        meta = _window_cells(snaps)
        meta["snaps"] = snaps
        windows[cid] = meta
    if not windows:
        raise ValueError(f"no windows found in {golden_dir} — nothing to cut")

    # --- Stratified selection ----------------------------------------------
    rng = random.Random(seed)
    cells: dict[tuple[str, int, str, str], list[str]] = defaultdict(list)
    for cid, w in windows.items():
        cells[(w["series"], w["duration"], w["day"], w["class"])].append(cid)
    for key in cells:
        cells[key].sort()  # deterministic cell membership
        rng.shuffle(cells[key])

    # Markets are market-DURATION pairs (series, duration) — the floor unit
    # the verify policy counts (`min_windows_per_market` per pair).
    n_markets = len({(w["series"], w["duration"]) for w in windows.values()})
    n_source_days = len({w["day"] for w in windows.values()})
    target_total = max(multiplier * FLOOR_WINDOWS, n_markets * FLOOR_WINDOWS_PER_MARKET)
    per_cell = _allocate(cells, target_total,
                         multiplier * FLOOR_WINDOWS_PER_MARKET)

    selected: list[str] = []
    for key, n in per_cell.items():
        selected.extend(cells[key][:n])
    selected.sort(key=lambda cid: windows[cid]["first_ts"])

    # --- Emission: raw-line copies, day-partitioned -------------------------
    # A window is attributed to the day of its first snap; its snaps are
    # written whole to that day's cut file, ordered by first_ts. Fresh `.idx`
    # sidecars are built eagerly per cut file (issue acceptance: `is_fresh`
    # must hold for every cut file) — one scan each, cheap next to ingestion.
    cid_to_day = {cid: w["day"] for cid, w in windows.items()}
    # The builder owns out_dir's cut files — and ONLY those. A non-empty dir
    # without a previous research manifest (e.g. a typo like --out run/ticks,
    # the PARENT of the golden dir) is refused before any delete, so no
    # unrelated collector files can never be wiped by a wrong flag.
    if out_dir.exists():
        existing = [p for p in out_dir.iterdir() if p.is_file()]
        if existing and not (out_dir / MANIFEST_NAME).is_file():
            raise ValueError(
                f"--out {out_dir} is non-empty and holds no {MANIFEST_NAME} "
                f"(not a previous research cut); refusing to delete its files")
        for p in (sorted(out_dir.glob("ticks_*.jsonl"))
                  + sorted(out_dir.glob("ticks_*.jsonl.idx"))
                  + [out_dir / MANIFEST_NAME]):
            if p.is_file():
                p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    per_day_lines: dict[str, list[str]] = defaultdict(list)
    for cid in selected:
        day = cid_to_day[cid]
        for snap in windows[cid]["snaps"]:
            raw = snap.get("_raw")
            if raw is None:  # defensive: parsed object without its raw text
                raw = json.dumps(snap)
            per_day_lines[day].append(raw)

    for day in sorted(per_day_lines):
        (out_dir / f"ticks_{day}.jsonl").write_text(
            "\n".join(per_day_lines[day]) + "\n", encoding="utf-8")
    for day in sorted(per_day_lines):
        build_index(out_dir / f"ticks_{day}.jsonl")

    # --- Guardrail 1: golden integrity --------------------------------------
    golden_after = {p.name: sha256_of(p) for p in sorted(golden_dir.glob("*.jsonl"))}
    golden_untouched = golden_before == golden_after

    # --- Guardrail 2: replay parity (same bytes ⇒ same WindowResult) --------
    # Across ALL selected windows: parity is a byte-identity property, so
    # every window must hold it. The helper re-reads the EMITTED files from
    # disk — comparing memory to itself would pass with broken emission.
    from backtest.engine import BacktestParams
    params = BacktestParams()
    parity_ok, compared = _replay_parity_from_disk(
        windows, out_dir, selected, params)

    # --- Metrics -------------------------------------------------------------
    # "Market" here is the verify policy's market-DURATION pair (series, duration)
    # — the floor `min_windows_per_market` is measured per pair, not per series.
    class_totals: dict[str, int] = defaultdict(int)
    per_market: dict[tuple[str, int], int] = defaultdict(int)
    days_rep: set[str] = set()
    series_rep: set[str] = set()
    for cid in selected:
        w = windows[cid]
        class_totals[w["class"]] += 1
        per_market[(w["series"], w["duration"])] += 1
        days_rep.add(w["day"])
        series_rep.add(w["series"])
    n_ticks = sum(len(per_day_lines[d]) for d in per_day_lines)
    min_per_market = min(per_market.values()) if per_market else 0

    # Day floor: the cut must represent (nearly) all source days. The "4" is
    # the charter floor for a full golden set; on a smaller golden the most we
    # can demand is every source day being present.
    day_floor = min(4, n_source_days)
    floors_ok = (len(selected) >= multiplier * FLOOR_WINDOWS
                 and min_per_market >= multiplier * FLOOR_WINDOWS_PER_MARKET
                 and len(days_rep) >= day_floor
                 and series_rep == {s for s, _d in _expected_market_pairs()})

    manifest = {
        "kind": "research_cut",
        "source": "golden",
        "source_dir": str(golden_dir),
        "multiplier": multiplier,
        "seed": seed,
        "policy": {
            "stratification": "series × day × window_class",
            "cell_allocation": "pair floors split proportionally across "
                               "cells (largest-remainder), min 1 per "
                               "non-empty cell",
            "window_attribution": "day of first snap; snaps written whole",
            "emission": "raw-line byte-identical copies",
        },
        "source_days": {
            name: {"sha256": sha} for name, sha in sorted(golden_before.items())
        },
        "totals": {
            "windows": len(selected),
            "min_windows_per_market": min_per_market,
            "days_represented": len(days_rep),
            "series_represented": len(series_rep),
            "valid_ticks": n_ticks,
            "skipped_lines": skipped_lines,
            "classes": dict(sorted(class_totals.items())),
        },
        "selection": {
            "selected_cids": selected,
            "cells": {f"{m}|{dur}|{d}|{c}": n
                      for (m, dur, d, c), n in sorted(per_cell.items())},
        },
        "guardrail": {
            "golden_untouched": golden_untouched,
            "replay_parity": parity_ok,
            "windows_compared": compared,
            "floors_met": floors_ok,
            "passed": golden_untouched and parity_ok and floors_ok,
        },
    }
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")

    if not manifest["guardrail"]["passed"]:
        raise RuntimeError(
            "research-cut guardrail FAILED: "
            f"golden_untouched={golden_untouched} replay_parity={parity_ok} "
            f"windows_compared={compared} floors_met={floors_ok}")
    return manifest


def _cid_of_line(line: str) -> str | None:
    """Best-effort cid of one raw tick line (kept for debugging/ad-hoc use; the
    single ingestion pass in build_research_cut parses lines directly)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    cid = obj.get("cid") if isinstance(obj, dict) else None
    return str(cid) if cid else None


def main() -> int:
    """CLI entry: parse flags, build the cut, print totals + next step."""
    ap = argparse.ArgumentParser(description="Build the golden dataset research cut (#312)")
    ap.add_argument("--golden", type=Path, default=ROOT / "run" / "ticks" / "golden",
                    help="golden dataset dir (read-only)")
    ap.add_argument("--out", type=Path, default=ROOT / "run" / "ticks" / "backtest",
                    help="output dir for the cut (the BACKTEST set)")
    ap.add_argument("--multiplier", type=int, default=DEFAULT_MULTIPLIER,
                    help="floor multiplier M (default 3: >=1500 windows, >=150/market)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED,
                    help="selection seed (default 0)")
    args = ap.parse_args()

    m = build_research_cut(args.golden, args.out, multiplier=args.multiplier,
                           seed=args.seed)
    t = m["totals"]
    print(f"research cut -> {args.out}")
    print(f"windows={t['windows']} min_per_market={t['min_windows_per_market']} "
          f"days={t['days_represented']} series={t['series_represented']} "
          f"ticks={t['valid_ticks']}")
    print(f"guardrail: {m['guardrail']}")
    print("next: python -m scripts.verify_tick_data "
          f"{args.out}  # expect RESEARCH_READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
