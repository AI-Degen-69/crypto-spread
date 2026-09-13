"""Replay cross-check driver for issue #146.

Replays the shadow night's tick coverage through the official backtest
engine (`backtest.engine._simulate_window` — the same per-window core the
dashboard `/api/backtest` uses) with the shadow's exact config mirrored,
scoped to the shadow universe + tick-coverage overlap. Writes
machine-readable totals for the paper-vs-replay comparison.

Scope rule: a window is INCLUDED iff its start_ts is inside tick coverage
[T0, T1], its first snap lands within STRICT_START_DELAY_SEC of the open
(T0-opening windows grandfathered), and no snap breaches the touch sanity
bounds. Trailing windows that close after T1 are kept — both paper
(stop-time rollover) and replay (last in-range bid) mark them to book
symmetrically.

Usage:
    python -m scripts.replay_shadow_check
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from backtest import BacktestParams, iter_ticks
from backtest.engine import _simulate_window, group_by_cid

ROOT = Path(__file__).resolve().parent.parent
TICKS_FILE = ROOT / "run" / "ticks" / "ticks_2026-09-12.jsonl"
SHADOW_DIR = ROOT / "runs" / "paper" / "2026-09-11_22-10_IDT"
OUT_DIR = SHADOW_DIR / "replay_comparison"

# Shadow universe (runs/paper/2026-09-11_22-10_IDT/data/meta.json).
UNIVERSE = ("xrp-up-or-down-15m", "bnb-up-or-down-15m", "eth-up-or-down-5m")

# T0 is the floor of tick coverage (first snap lands 2s later at 00:00:02Z);
# START_TOL_SEC absorbs that sub-poll offset so windows opening exactly at
# coverage start count as fully observed. T1 is the shadow stop time
# (data/final.json stopped_utc).
T0 = datetime.datetime(2026, 9, 12, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
T1 = datetime.datetime(2026, 9, 12, 9, 10, 58, tzinfo=datetime.timezone.utc).timestamp()
START_TOL_SEC = 1.0

# Strict full-window rule (dashboard "Strict Full Windows"): first snap lands
# within 2s of the open. Windows opening exactly at coverage start are
# grandfathered (their <=2.1s blind prefix is immaterial — delay-60 configs
# never quote before 60s anyway).
STRICT_START_DELAY_SEC = 2.0

# Touch-pair sanity bounds (verify_tick_data "sane bounds"): any window with
# a snap outside is quarantined from the comparison (dislocated/stale book).
TOUCH_LO, TOUCH_HI = 0.50, 1.50

# Pair-cost gate values: 0.98 = paper preset transcription (live-touch
# semantics make it a near-total quote block — kept for reference);
# 1.05 = engine default + research §5 value (dislocation filter).
PAIR_CAPS = (0.98, 1.05)

SHARES = 5


def build_params(fill_model: str = "tape", gates_on: bool = True,
                 pair_cap: float = 0.98) -> BacktestParams:
    """Mirror the shadow final.json params into engine knobs (issue #146 §1).

    The prescribed verdict leg is fill_model="tape" with gates on. The other
    three legs of the 2x2 matrix (book x gates) attribute divergence to the
    fill model vs the delay/band gates.
    """
    return BacktestParams(
        offset=0.03,
        queue_gate=0.0,
        pair_cost_gate=pair_cap,
        exit_thresh_by_slug={
            "default_5m": 0.05,
            "default_15m": 0.05,
            "xrp-up-or-down-15m": 0.05,
            "bnb-up-or-down-15m": 0.05,
            "eth-up-or-down-5m": 0.05,
        },
        exit_reversal=0.5,
        quote_shares=SHARES,
        fill_model=fill_model,
        merge_gas_usd=0.0,
        max_start_delay_sec=0.0,
        entry_timeout_pct=1.0,
        max_start_elapsed_pct=0.1,
        reentry_drift_band=0.015,
        min_requote_remaining_sec=300.0,
        reentry_min_remaining_pct=0.3,
        max_reentries_per_window=0,
        entry_delay_sec=60.0 if gates_on else 0.0,
        entry_band=0.04 if gates_on else 0.0,
    )


def load_shadow_recorded(path: Path | None = None) -> dict:
    """Read the shadow run's recorded params (data/final.json).

    Single source of truth for the config mirror: expected values are derived
    from what the shadow actually ran, never re-typed. `path` is injectable so
    tests stay hermetic (runs/ is gitignored and absent on fresh clones).
    """
    src = path or (SHADOW_DIR / "data" / "final.json")
    raw = json.loads(src.read_text(encoding="utf-8"))
    return raw["params"]


def assert_config_mirror(params: BacktestParams, recorded: dict,
                         fill_model: str, gates_on: bool,
                         pair_cap: float = 0.98) -> None:
    """Fail fast if any mirrored knob drifts from the recorded shadow config.

    Field renames recorded -> engine: max_pair_cost -> pair_cost_gate,
    shares -> quote_shares. fill_model/queue_gate/merge_gas_usd have no recorded
    equivalent (paper fills on touch; replay-side documented choices).
    pair_cap 1.05 is a deliberate research-§5 override, not a transcription.
    """
    pairs = [
        ("offset", recorded["offset"]),
        ("pair_cost_gate", pair_cap),
        ("exit_reversal", recorded["exit_reversal"]),
        ("quote_shares", recorded["shares"]),
        ("entry_timeout_pct", recorded["entry_timeout_pct"]),
        ("max_start_elapsed_pct", recorded["max_start_elapsed_pct"]),
        ("reentry_drift_band", recorded["reentry_drift_band"]),
        ("min_requote_remaining_sec", recorded["min_requote_remaining_sec"]),
        ("reentry_min_remaining_pct", recorded["reentry_min_remaining_pct"]),
        ("max_reentries_per_window", recorded["max_reentries_per_window"]),
        ("entry_delay_sec", recorded["entry_delay_sec"] if gates_on else 0.0),
        ("entry_band", recorded["entry_band"] if gates_on else 0.0),
    ]
    for field, want in pairs:
        got = getattr(params, field)
        assert got == want, f"config mirror broken: {field}={got!r} want {want!r}"
    assert params.fill_model == fill_model, "fill leg mismatch"
    assert params.queue_gate == 0.0, "queue gate must stay off (paper has none)"
    assert params.merge_gas_usd == 0.0, "merge gas must stay 0 (gasless merges)"
    assert recorded["exit_thresh"] == 0.05 and recorded["exit_thresh_naked"] == 0.05
    for key, want in (("default_5m", 0.05), ("default_15m", 0.05)):
        assert params.exit_thresh_by_slug.get(key) == want, f"exit mirror gap: {key}"
    for slug in UNIVERSE:
        assert params.exit_thresh_by_slug.get(slug) == 0.05, f"exit mirror gap: {slug}"


def load_scoped_snaps() -> list[dict]:
    """Stream the tick file, keeping only universe snaps inside [T0, T1]."""
    kept: list[dict] = []
    for snap in iter_ticks(TICKS_FILE):
        if snap.get("series") not in UNIVERSE:
            continue
        ts = float(snap.get("ts", 0.0) or 0.0)
        if T0 <= ts <= T1:
            kept.append(snap)
    assert kept, "scope filter empty: no universe snaps in [T0, T1]"
    return kept


def snap_touch(snap: dict) -> float | None:
    """Live touch pair (up_ask + dn_ask) for one snap, or None if unknowable."""
    touch = snap.get("touch_pair")
    try:
        touch_f = float(touch) if touch is not None else None
    except (TypeError, ValueError):
        touch_f = None
    if touch_f is not None:
        return touch_f
    ub, db = snap.get("up_book") or {}, snap.get("down_book") or {}
    up_ask, dn_ask = ub.get("best_ask"), db.get("best_ask")
    if up_ask is None or dn_ask is None:
        return None
    return float(up_ask) + float(dn_ask)


def select_groups(snaps: list[dict]) -> tuple[list[tuple[str, list[dict]]], dict]:
    """Keep fully-observed, sane-book windows; count exclusions by reason."""
    included: list[tuple[str, list[dict]]] = []
    excluded = {"pre_coverage": 0, "strict_late": 0, "touch_insane": 0}
    for cid, group in group_by_cid(snaps):
        start_ts = float(group[0].get("start_ts", 0.0) or 0.0)
        if start_ts < T0 - START_TOL_SEC:
            excluded["pre_coverage"] += 1
            continue
        first_delay = float(group[0].get("ts", 0.0) or 0.0) - start_ts
        grandfathered = abs(start_ts - T0) <= START_TOL_SEC
        if first_delay > STRICT_START_DELAY_SEC and not grandfathered:
            excluded["strict_late"] += 1
            continue
        if any((t is not None and not (TOUCH_LO <= t <= TOUCH_HI))
               for t in (snap_touch(s) for s in group)):
            excluded["touch_insane"] += 1
            continue
        included.append((cid, group))
    assert included, "no fully-observed windows in scope"
    return included, excluded


def summarize(params: BacktestParams, groups: list[tuple[str, list[dict]]]) -> dict:
    """Simulate each included window and aggregate pair/settle/exit totals."""
    pairs = settles = exits = no_event = 0
    gross_cents = fees_cents = 0.0
    reentries = 0
    per_series: dict[str, dict] = {}
    windows: list[dict] = []
    for cid, group in groups:
        w = _simulate_window(group, params)
        reentries += w.reentry_count
        gross_cents += w.pnl_cents
        fees_cents += w.fees_cents
        single_leg = (w.filled_up and not w.filled_down) or (w.filled_down and not w.filled_up)
        if w.pair_captured:
            kind = "pair"
            pairs += 1
        elif w.exit_taken:
            kind = "exit"
            exits += 1
        elif single_leg:
            kind = "settle"
            settles += 1
        else:
            kind = "no_event"
            no_event += 1
        start_ts = float(group[0].get("start_ts", 0.0) or 0.0)
        windows.append({
            "series": w.series,
            "window_start": int(start_ts),
            "market_slug": w.slug,
            "kind": kind,
            "pnl_cents": w.pnl_cents,
            "gross_usd": round(w.pnl_cents * SHARES / 100.0, 4),
            "entry_up": w.entry_price_up,
            "entry_down": w.entry_price_down,
            "exit_price": w.exit_price,
            "settlement_mid": w.settlement_mid,
            "is_partial": w.is_partial,
        })
        s = per_series.setdefault(w.series, {"windows": 0, "pairs": 0, "settles": 0,
                                             "exits": 0, "gross_cents": 0.0})
        s["windows"] += 1
        s["gross_cents"] = round(s["gross_cents"] + w.pnl_cents, 4)
        if kind == "pair":
            s["pairs"] += 1
        elif kind == "settle":
            s["settles"] += 1
        elif kind == "exit":
            s["exits"] += 1
    assert reentries == 0, f"re-entry fired {reentries}x despite max_reentries=0"
    events = pairs + settles + exits
    gross_usd = round(gross_cents * SHARES / 100.0, 4)
    return {
        "n_included_windows": len(groups),
        "pairs": pairs,
        "settles": settles,
        "exits": exits,
        "no_event_windows": no_event,
        "n_events": events,
        "replay_gross_cents": round(gross_cents, 4),
        "replay_fees_cents": round(fees_cents, 4),
        "replay_gross_usd": gross_usd,
        "expectancy_usd_per_event": round(gross_usd / events, 4) if events else 0.0,
        "per_series": per_series,
        "windows": windows,
    }


def require_tick_verification() -> dict:
    """Enforce the Task-1 integrity gate via its file-bound artifact.

    The verifier scan is expensive (700MB), so main() validates the recorded
    report instead of re-scanning: same ticks file, no FAIL status, zero
    corrupt lines. Raises with the exact remediation command otherwise.
    """
    path = OUT_DIR / "verify_ticks.json"
    hint = ("run: python -m scripts.verify_tick_data "
            "run/ticks/ticks_2026-09-12.jsonl --json > " + str(path))
    if not path.exists():
        raise RuntimeError(f"missing tick verification artifact {path}; {hint}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("file") != TICKS_FILE.name:
        raise RuntimeError(f"verification artifact is for {report.get('file')}, "
                           f"not {TICKS_FILE.name}; {hint}")
    if report.get("status") == "FAIL" or report.get("corrupt_lines"):
        raise RuntimeError(f"tick data failed verification ({path}); refusing replay")
    return report


def main() -> None:
    """Run the scoped replay legs and write replay_totals.json."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    verification = require_tick_verification()
    recorded = load_shadow_recorded()
    snaps = load_scoped_snaps()
    groups, excluded = select_groups(snaps)
    legs: dict[str, dict] = {}
    for fill_model, gates_on, pair_cap in (
            ("tape", True, 0.98), ("book", True, 0.98),
            ("tape", False, 0.98), ("book", False, 0.98),
            ("tape", True, 1.05), ("book", True, 1.05),
            ("tape", False, 1.05), ("book", False, 1.05)):
        params = build_params(fill_model, gates_on, pair_cap)
        assert_config_mirror(params, recorded, fill_model, gates_on, pair_cap)
        totals = summarize(params, groups)
        totals["params_hash"] = params.params_hash()
        leg = f"{fill_model}_{'gates' if gates_on else 'nogates'}_pc{pair_cap}"
        legs[leg] = totals
        print(f"[{leg}] "
              f"windows={totals['n_included_windows']} pairs={totals['pairs']} "
              f"settles={totals['settles']} exits={totals['exits']} "
              f"gross_usd={totals['replay_gross_usd']}")
    if sum(t["n_events"] for t in legs.values()) == 0:
        raise RuntimeError("all replay legs empty — data flow broken, refusing artifact")
    out_payload = {
        "verdict_leg": "tape_gates_pc1.05",
        "legs": legs,
        "scope": {
            "ticks_file": TICKS_FILE.name,
            "tick_verification_status": verification.get("status"),
            "universe": list(UNIVERSE),
            "t0_utc": "2026-09-12T00:00:00Z",
            "t1_utc": "2026-09-12T09:10:58Z",
            "strict_start_delay_sec": STRICT_START_DELAY_SEC,
            "touch_bounds": [TOUCH_LO, TOUCH_HI],
            "pair_caps": list(PAIR_CAPS),
            "n_snaps_scoped": len(snaps),
            "n_tick_windows_excluded": excluded,
            "n_shadow_events_excluded_pre_coverage": 11,
        },
        "accounting": (
            "gross pnl_cents per share x 5 shares / 100 = USD; "
            "engine taker fees tracked separately in replay_fees_cents and "
            "EXCLUDED from the paper comparison (paper books pairs/settles gross)."
        ),
    }
    out = OUT_DIR / "replay_totals.json"
    out.write_text(json.dumps(out_payload, indent=1), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
