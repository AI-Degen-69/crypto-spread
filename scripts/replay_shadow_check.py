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
        # bare: frozen #146 reproduction (module defaults below)
    python -m scripts.replay_shadow_check --run runs/paper/<id> [--ticks <jsonl>]
        # scope an arbitrary run: UNIVERSE + T0 from its data/meta.json,
        # T1 from data/final.json (stopped_utc), OUT_DIR inside the run dir
        # (issue #465 T2).
"""
from __future__ import annotations

import argparse
import dataclasses
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

# Chase-ceiling values (issue #227: the cap bounds the leg chase only, and
# 1.00 is its hard maximum because a binary pair settles at 1.00): 0.98 = the
# paper preset transcription; 1.00 = the loosest cap the engine will accept.
# The leg that was pc1.05 is pc1.00 -- the same "cap as loose as it goes" role.
PAIR_CAPS = (0.98, 1.00)

SHARES = 5


@dataclasses.dataclass(frozen=True)
class RunScope:
    """Everything the driver scopes to, resolved for one run.

    The module constants above are the frozen #146 reproduction values;
    a scope derived from a run dir (derive_scope) replaces them wholesale
    so any run can be cross-checked, not just the recorded one.
    """
    ticks_file: Path
    shadow_dir: Path
    out_dir: Path
    universe: tuple[str, ...]
    t0: float
    t1: float


def default_scope() -> RunScope:
    """The frozen #146 scope — a bare invocation stays byte-identical."""
    return RunScope(
        ticks_file=TICKS_FILE,
        shadow_dir=SHADOW_DIR,
        out_dir=OUT_DIR,
        universe=UNIVERSE,
        t0=T0,
        t1=T1,
    )


def _parse_utc(stamp: str) -> float:
    """Parse an ISO-8601 UTC timestamp (meta.json/final.json format) to epoch seconds."""
    return datetime.datetime.fromisoformat(stamp).timestamp()


def derive_scope(run_dir: Path, ticks_file: Path | None = None) -> RunScope:
    """Resolve the scope from a run's own artifacts (issue #465 T2).

    UNIVERSE and the start stamp T0 come from data/meta.json (written at
    run start); T1 comes from data/final.json stopped_utc (written at stop);
    OUT_DIR stays inside the run. The pilot records no started_utc in
    final.json, so both files are read for the start stamp — a run dir in
    either shape derives a scope.
    Without an explicit ticks file, the coverage file named for the stop
    date is required (the overnight pattern: collection floors at midnight).
    """
    meta = json.loads((run_dir / "data" / "meta.json").read_text(encoding="utf-8"))
    final = json.loads((run_dir / "data" / "final.json").read_text(encoding="utf-8"))
    universe = tuple(meta["config_hypothesis"]["universe"])
    started = meta.get("started_utc") or final.get("started_utc")
    if not started:
        raise RuntimeError(
            f"run {run_dir}: no start stamp — expected started_utc in "
            f"data/meta.json or data/final.json")
    if not final.get("stopped_utc"):
        raise RuntimeError(
            f"run {run_dir}: no stop stamp — expected stopped_utc in "
            f"data/final.json")
    t0 = _parse_utc(started)
    t1 = _parse_utc(final["stopped_utc"])
    if not t1 > t0:
        raise RuntimeError(
            f"run {run_dir}: stopped_utc must postdate started_utc")
    if ticks_file is None:
        stop_day = datetime.datetime.fromtimestamp(t1, tz=datetime.timezone.utc)
        ticks_file = ROOT / "run" / "ticks" / f"ticks_{stop_day:%Y-%m-%d}.jsonl"
        if not ticks_file.exists():
            raise RuntimeError(
                f"no ticks file for the stop day: expected {ticks_file.name} "
                f"in {ticks_file.parent}; pass --ticks explicitly")
    return RunScope(
        ticks_file=ticks_file,
        shadow_dir=run_dir,
        out_dir=run_dir / "replay_comparison",
        universe=universe,
        t0=t0,
        t1=t1,
    )


def resolve_scope(run_arg: str, ticks_arg: str) -> RunScope:
    """CLI scope resolution: bare = frozen #146; --run derives; --ticks overrides.

    Only --ticks (no --run) keeps the frozen universe/times and swaps the
    coverage file — a deliberate re-scope of the #146 reproduction.
    """
    if run_arg:
        return derive_scope(Path(run_arg),
                            Path(ticks_arg) if ticks_arg else None)
    if ticks_arg:
        return dataclasses.replace(default_scope(), ticks_file=Path(ticks_arg))
    return default_scope()


def build_params(gates_on: bool = True,
                 pair_cap: float = 0.98,
                 naked_leg_at_expiry: str = "hold") -> BacktestParams:
    """Mirror the shadow final.json params into engine knobs (issue #146 §1).

    The prescribed verdict leg has the gates on. The fill model used to be the
    other axis of a 2x2 matrix; issue #226 left one fill rule, so the legs are
    now gates x pair cap and divergence can only be attributed to those.

    `naked_leg_at_expiry` defaults to "hold" — the recorded #146 shadow ran
    hold-to-settle, so the frozen reproduction stays byte-identical. main()
    passes the run's own recorded value, so a fresh run ("close", #223) is
    replayed under the policy it actually ran.
    """
    return BacktestParams(
        offset=0.03,
        queue_gate=0.0,
        max_pair_cost=pair_cap,
        exit_thresh_by_slug={
            "default_5m": 0.05,
            "default_15m": 0.05,
            "xrp-up-or-down-15m": 0.05,
            "bnb-up-or-down-15m": 0.05,
            "eth-up-or-down-5m": 0.05,
        },
        exit_reversal=0.5,
        quote_shares=SHARES,
        merge_gas_usd=0.0,
        entry_delay_sec=60.0 if gates_on else 0.0,
        naked_leg_at_expiry=naked_leg_at_expiry,
        # Issue #229: the deleted timeout/late-start clocks are gone; the
        # shadow dead zone (default 0.10 pct) is mirrored implicitly — a
        # drift there now fails the mirror like any other knob.
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
                         gates_on: bool,
                         pair_cap: float = 0.98) -> None:
    """Fail fast if any mirrored knob drifts from the recorded shadow config.

    Field renames recorded -> engine: shares -> quote_shares. `max_pair_cost`
    is now one word in both (issue #227). queue_gate/merge_gas_usd have no
    recorded equivalent (replay-side documented choices).
    pair_cap 1.00 is a deliberate loosest-cap override, not a transcription.
    `naked_leg_at_expiry` is compared against the recorded config when the
    run records it (it does), defaulting to the #146 "hold" otherwise.
    """
    pairs = [
        ("offset", recorded["offset"]),
        ("max_pair_cost", pair_cap),
        ("exit_reversal", recorded["exit_reversal"]),
        ("quote_shares", recorded["shares"]),
        ("entry_delay_sec", recorded["entry_delay_sec"] if gates_on else 0.0),
    ]
    for field, want in pairs:
        got = getattr(params, field)
        assert got == want, f"config mirror broken: {field}={got!r} want {want!r}"
    want_naked = str(recorded.get("naked_leg_at_expiry", "hold"))
    assert params.naked_leg_at_expiry == want_naked, (
        f"naked-leg mirror broken: {params.naked_leg_at_expiry!r} want {want_naked!r}")
    assert params.queue_gate == 0.0, "queue gate must stay off (paper has none)"
    assert params.merge_gas_usd == 0.0, "merge gas must stay 0 (gasless merges)"
    assert recorded["exit_thresh"] == 0.05
    for key, want in (("default_5m", 0.05), ("default_15m", 0.05)):
        assert params.exit_thresh_by_slug.get(key) == want, f"exit mirror gap: {key}"
    for slug in UNIVERSE:
        assert params.exit_thresh_by_slug.get(slug) == 0.05, f"exit mirror gap: {slug}"


def load_scoped_snaps(scope: RunScope | None = None) -> list[dict]:
    """Stream the tick file, keeping only universe snaps inside [T0, T1]."""
    scope = scope or default_scope()
    kept: list[dict] = []
    for snap in iter_ticks(scope.ticks_file):
        if snap.get("series") not in scope.universe:
            continue
        ts = float(snap.get("ts", 0.0) or 0.0)
        if scope.t0 <= ts <= scope.t1:
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


def select_groups(snaps: list[dict],
                  scope: RunScope | None = None) -> tuple[list[tuple[str, list[dict]]], dict]:
    """Keep fully-observed, sane-book windows; count exclusions by reason."""
    scope = scope or default_scope()
    included: list[tuple[str, list[dict]]] = []
    excluded = {"pre_coverage": 0, "strict_late": 0, "touch_insane": 0}
    for cid, group in group_by_cid(snaps):
        start_ts = float(group[0].get("start_ts", 0.0) or 0.0)
        if start_ts < scope.t0 - START_TOL_SEC:
            excluded["pre_coverage"] += 1
            continue
        first_delay = float(group[0].get("ts", 0.0) or 0.0) - start_ts
        grandfathered = start_ts == scope.t0
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


def require_tick_verification(scope: RunScope | None = None) -> dict:
    """Enforce the Task-1 integrity gate via its file-bound artifact.

    The verifier scan is expensive (700MB), so main() validates the recorded
    report instead of re-scanning: same ticks file, no FAIL status, zero
    corrupt lines. Raises with the exact remediation command otherwise.
    """
    scope = scope or default_scope()
    path = scope.out_dir / "verify_ticks.json"
    hint = ("run: python -m scripts.verify_tick_data "
            f"{scope.ticks_file} --json > " + str(path))
    if not path.exists():
        raise RuntimeError(f"missing tick verification artifact {path}; {hint}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("file") != scope.ticks_file.name:
        raise RuntimeError(f"verification artifact is for {report.get('file')}, "
                           f"not {scope.ticks_file.name}; {hint}")
    if report.get("status") == "FAIL" or report.get("corrupt_lines"):
        raise RuntimeError(f"tick data failed verification ({path}); refusing replay")
    return report


def main() -> None:
    """Run the scoped replay legs and write replay_totals.json."""
    ap = argparse.ArgumentParser(
        description="Replay cross-check of a shadow paper run (issues #146, #465)")
    ap.add_argument("--run", type=str, default="",
                    help="runs/paper/<id> to cross-check "
                         "(default: the frozen 2026-09-11_22-10_IDT reproduction)")
    ap.add_argument("--ticks", type=str, default="",
                    help="ticks jsonl to scope "
                         "(default with --run: run/ticks/ticks_<stop-date>.jsonl)")
    args = ap.parse_args()
    scope = resolve_scope(args.run, args.ticks)
    scope.out_dir.mkdir(parents=True, exist_ok=True)
    verification = require_tick_verification(scope)
    recorded = load_shadow_recorded(
        scope.shadow_dir / "data" / "final.json")
    naked = str(recorded.get("naked_leg_at_expiry", "hold"))
    snaps = load_scoped_snaps(scope)
    groups, excluded = select_groups(snaps, scope)
    legs: dict[str, dict] = {}
    for gates_on, pair_cap in (
            (True, PAIR_CAPS[0]), (False, PAIR_CAPS[0]),
            (True, PAIR_CAPS[1]), (False, PAIR_CAPS[1])):
        params = build_params(gates_on, pair_cap, naked_leg_at_expiry=naked)
        assert_config_mirror(params, recorded, gates_on, pair_cap)
        totals = summarize(params, groups)
        totals["params_hash"] = params.params_hash()
        leg = f"{'gates' if gates_on else 'nogates'}_pc{pair_cap}"
        legs[leg] = totals
        print(f"[{leg}] "
              f"windows={totals['n_included_windows']} pairs={totals['pairs']} "
              f"settles={totals['settles']} exits={totals['exits']} "
              f"gross_usd={totals['replay_gross_usd']}")
    if sum(t["n_events"] for t in legs.values()) == 0:
        raise RuntimeError("all replay legs empty — data flow broken, refusing artifact")
    out_payload = {
        "verdict_leg": "gates_pc1.0",
        "legs": legs,
        "scope": {
            "ticks_file": scope.ticks_file.name,
            "tick_verification_status": verification.get("status"),
            "universe": list(scope.universe),
            "t0_utc": datetime.datetime.fromtimestamp(
                scope.t0, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "t1_utc": datetime.datetime.fromtimestamp(
                scope.t1, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    out = scope.out_dir / "replay_totals.json"
    out.write_text(json.dumps(out_payload, indent=1), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
