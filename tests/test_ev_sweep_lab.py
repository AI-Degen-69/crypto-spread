"""Regression tests for the EV sweep lab (issue #182).

The lab is the code that produced `patient_band_maker`, and the next
collection run replays it against fresh ticks. These tests pin the defects
found on PR #181 so a rerun cannot silently reintroduce them.

Everything here runs without `run/sweeps/window_cache.pkl` — that cache is
derived from `run/ticks/`, both are gitignored, and neither exists on a clean
checkout or in CI.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SWEEPS = ROOT / "research" / "sweeps"


def _load(name: str):
    """Import a sweeps module by path (the directory is not a package)."""
    if str(SWEEPS) not in sys.path:
        sys.path.insert(0, str(SWEEPS))
    spec = importlib.util.spec_from_file_location(name, SWEEPS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


ev_lab = _load("ev_lab")


# ---------------------------------------------------------------------------
# 1. ROI unit mismatch
# ---------------------------------------------------------------------------

def _rows(pnls_cents, capital=100.0, day="2026-09-11"):
    """Minimal `summarize` input rows carrying a given per-window net PnL."""
    return [{"pnl": c, "fees": 0.0, "pair": False, "exit": False,
             "filled_up": False, "filled_dn": False, "naked_none": False,
             "capital": capital, "day": day, "series": "eth-up-or-down-5m",
             "duration": 300, "class_label": "osc"}
            for c in pnls_cents]


def test_roi_is_a_percentage_of_capital_not_a_hundredfold_of_one():
    """`total` is cents and `cap` is USD; dividing them raw inflated ROI 100x.

    Deliberately arithmetic rather than golden-value. Ten windows of +2c each
    at size 5 return 10*2*5 = 100c = $1, against capital of
    10 windows * 100c * 5 / 100 = $50 — so ROI is 2%. The pre-fix expression
    divided the raw cents figure by the USD capital and reported 200%.
    """
    s = ev_lab.summarize(_rows([2.0] * 10), size=5, n_boot=50)
    assert s["total_pnl_usd"] == pytest.approx(1.0)
    assert s["capital_usd"] == pytest.approx(50.0)
    assert s["roi_pct_per_window"] == pytest.approx(2.0)
    # The relationship must hold identically, whatever the inputs.
    assert s["roi_pct_per_window"] == pytest.approx(
        s["total_pnl_usd"] / s["capital_usd"] * 100.0)


def test_roi_is_zero_when_no_capital_was_deployed():
    """No capital means no ratio; it must not raise or report a number."""
    assert ev_lab.summarize(_rows([1.0], capital=0.0),
                            size=5, n_boot=50)["roi_pct_per_window"] == 0.0


# ---------------------------------------------------------------------------
# 2. Bootstrap seed reproducibility
# ---------------------------------------------------------------------------

def test_stable_seed_does_not_depend_on_the_process_hash_salt():
    """`hash()` on a str is salted per process; the published CIs were seeded
    with it, so they could never be reproduced by a rerun."""
    assert ev_lab.stable_seed("phase5:band0.04:delay60") == \
        ev_lab.stable_seed("phase5:band0.04:delay60")
    assert 0 <= ev_lab.stable_seed("anything") <= 0xFFFF
    assert ev_lab.stable_seed("a") != ev_lab.stable_seed("b")
    # Pinned so a future refactor of the digest is a visible, deliberate change
    # rather than a silent invalidation of every published bound.
    import zlib
    assert ev_lab.stable_seed("baseline") == \
        zlib.crc32(b"baseline") & 0xFFFF


def test_seeded_summaries_are_reproducible_across_calls():
    """Same rows, same seed, identical confidence bounds."""
    rows = _rows([1.0, -2.0, 3.0, 0.5, -0.25] * 8)
    a = ev_lab.summarize(rows, size=5, n_boot=200,
                         seed=ev_lab.stable_seed("cfg-x"))
    b = ev_lab.summarize(rows, size=5, n_boot=200,
                         seed=ev_lab.stable_seed("cfg-x"))
    assert (a["ci95_lo"], a["ci95_hi"]) == (b["ci95_lo"], b["ci95_hi"])


def test_no_sweep_script_seeds_a_bootstrap_with_builtin_hash():
    """The salted-seed bug must not creep back into any phase driver."""
    offenders = [p.name for p in SWEEPS.glob("*.py")
                 if "seed=hash(" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"salted bootstrap seed reintroduced in {offenders}"


def _undefined_globals(path: Path) -> set:
    """Names a module calls at runtime that it never binds or imports.

    A miniature pyflakes: collect every bare name in call position, subtract
    builtins, module-level bindings, imports, and anything bound inside the
    enclosing function (parameters, assignments, comprehension targets, `with`
    and `except` names). What remains would raise `NameError` when that line
    finally executes.
    """
    import ast
    import builtins

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound = set(dir(builtins))
    called: set = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                for a in (list(args.args) + list(args.posonlyargs)
                          + list(args.kwonlyargs)
                          + [args.vararg, args.kwarg]):
                    if a is not None:
                        bound.add(a.arg)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            bound.update(node.names)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    return called - bound


def test_every_sweep_script_can_resolve_the_functions_it_calls():
    """A converted call site is useless if the name was never imported.

    Found by review: three drivers had `seed=hash(...)` replaced with
    `seed=stable_seed(...)` while their `from ev_lab import ...` line was left
    untouched. Each would have raised `NameError` only at the final
    `summarize()` call — after the whole multiprocessing sweep had already run
    and with no results written. The string-grep test above stayed green,
    because the offending string really was gone.
    """
    offenders = {}
    for path in sorted(SWEEPS.glob("*.py")):
        missing = _undefined_globals(path)
        if missing:
            offenders[path.name] = sorted(missing)
    assert offenders == {}, (
        "these drivers call names they never bind — they would NameError at "
        f"runtime: {offenders}")


# ---------------------------------------------------------------------------
# 6. Knobs the fast simulator does not implement
# ---------------------------------------------------------------------------

def test_fast_simulate_refuses_knobs_it_would_otherwise_ignore():
    """`entry_delay_sec`/`entry_band` define patient_band_maker and were never
    implemented here, so the parity claim never covered the shipped preset."""
    from backtest.engine import BacktestParams
    for kwargs in ({"entry_delay_sec": 60.0}, {"entry_band": 0.04},
                   {"entry_delay_sec": 60.0, "entry_band": 0.04}):
        with pytest.raises(ValueError, match="fast_simulate does not implement"):
            ev_lab._reject_unsupported_knobs(BacktestParams(**kwargs))
    # Zero means "off" and must stay accepted.
    ev_lab._reject_unsupported_knobs(
        BacktestParams(entry_delay_sec=0.0, entry_band=0.0))


def _one_tick_window():
    """Smallest `Win` `fast_simulate` will accept, for guard-path tests."""
    from array import array
    book = array("d", [0.48, 100.0])
    return ev_lab.Win({
        "cid": "0xtest", "series": "eth-up-or-down-5m",
        "slug": "eth-up-or-down-5m", "duration": 300, "start_ts": 1_760_000_000.0,
        "day": "2026-09-11", "ts": [1_760_000_000.0], "s_mid": [0.50],
        "up_bb": [0.49], "up_ba": [0.51], "dn_bb": [0.49], "dn_ba": [0.51],
        "up_bids": [book], "dn_bids": [book], "tape": [([], [])],
        "up_token": "tok_up", "dn_token": "tok_dn",
    })


def test_fast_simulate_itself_enforces_the_guard():
    """Asserting on the helper alone would pass even if nothing called it.

    Found by reverting the call site: every other test still went green.
    """
    from backtest.engine import BacktestParams
    w = _one_tick_window()
    with pytest.raises(ValueError, match="fast_simulate does not implement"):
        ev_lab.fast_simulate(w, BacktestParams(entry_delay_sec=60.0))
    with pytest.raises(ValueError, match="fast_simulate does not implement"):
        ev_lab.fast_simulate(w, BacktestParams(entry_band=0.04))
    # An unaffected config still simulates.
    assert isinstance(ev_lab.fast_simulate(w, BacktestParams()), dict)


# ---------------------------------------------------------------------------
# 5. Trade side in the cache
# ---------------------------------------------------------------------------

def test_print_side_is_classified_from_the_snapshot_book():
    """A buy that lifted the ask cannot fill our resting bid under tapeq."""
    assert ev_lab._classify_side(0.52, 0.48, 0.52) == ev_lab.SIDE_BUY
    assert ev_lab._classify_side(0.55, 0.48, 0.52) == ev_lab.SIDE_BUY
    assert ev_lab._classify_side(0.48, 0.48, 0.52) == ev_lab.SIDE_SELL
    assert ev_lab._classify_side(0.45, 0.48, 0.52) == ev_lab.SIDE_SELL
    # Inside an untouched spread the aggressor is unattributable; SIDE_SELL
    # keeps the print eligible to fill, matching the previous default.
    assert ev_lab._classify_side(0.50, 0.48, 0.52) == ev_lab.SIDE_SELL
    # A missing or malformed book must never raise on the cache-build path.
    assert ev_lab._classify_side(0.50, None, None) == ev_lab.SIDE_SELL
    assert ev_lab._classify_side(0.50, "x", "y") == ev_lab.SIDE_SELL


def test_cache_version_is_bumped_past_the_sideless_shape():
    """v1 pickles stored `(px, sz)`; loading one as v2 would read every print
    as a sell, which is exactly the bug the side field fixes."""
    assert ev_lab.CACHE_VERSION >= 2


def test_sim2_and_fast_simulate_agree_on_the_buy_side_constant():
    """Both simulators must filter the same value, or tapeq results diverge."""
    src = (SWEEPS / "sim2.py").read_text(encoding="utf-8")
    assert "tside == 1" in src
    assert ev_lab.SIDE_BUY == 1


# ---------------------------------------------------------------------------
# 8 / 9. Reporting robustness
# ---------------------------------------------------------------------------

def test_break_even_config_is_not_treated_as_missing():
    """`0.0 or -9e9` is -9e9, so break-even sorted below every loss."""
    results = [{"name": "loss", "total_pnl_usd": -5.0},
               {"name": "flat", "total_pnl_usd": 0.0},
               {"name": "win", "total_pnl_usd": 3.0},
               {"name": "unrun", "total_pnl_usd": None}]
    results.sort(key=lambda r: -(r["total_pnl_usd"]
                                 if r.get("total_pnl_usd") is not None else -9e9))
    assert [r["name"] for r in results] == ["win", "flat", "loss", "unrun"]


def test_no_sweep_script_still_uses_the_falsy_pnl_sort():
    """Pin the fix across every phase driver, not just the one that was cited."""
    offenders = [p.name for p in SWEEPS.glob("*.py")
                 if 'get("total_pnl_usd") or -9e9' in p.read_text(encoding="utf-8")]
    assert offenders == [], f"falsy-PnL sort reintroduced in {offenders}"


def test_empty_selection_summary_has_the_full_report_shape():
    """A series absent from the cache used to KeyError the whole report after
    an expensive sweep had already finished."""
    empty = ev_lab.summarize([], size=5, n_boot=50)
    assert empty["n"] == 0
    real = ev_lab.summarize(_rows([1.0, -1.0]), size=5, n_boot=50)
    missing = sorted(set(real) - set(empty))
    assert missing == [], f"empty summary is missing report keys: {missing}"
    # Rates read as "0 of 0"; CI bounds stay None because none was estimated,
    # and a 0.0 there would read as a measured bound.
    assert empty["pair_rate"] == 0.0 and empty["win_rate"] == 0.0
    assert empty["ci95_lo"] is None and empty["ci95_day_lo"] is None


def test_every_phase_report_row_survives_an_empty_summary():
    """The formatting strings index these keys directly."""
    empty = ev_lab.summarize([], size=5, n_boot=50)
    for key in ("n", "pair_rate", "exit_rate", "win_rate", "total_pnl_usd",
                "mean_net_cents", "ci95_lo", "profit_factor"):
        assert key in empty, f"phase reports index {key!r}"


# ---------------------------------------------------------------------------
# 7. Documented Phase 1 baseline
# ---------------------------------------------------------------------------

def test_phase1_baseline_matches_the_documented_one():
    """The sweep reports against `ex_5m=0.08, rev=0.015`; the engine defaults
    are 0.05/0.02, so an implicit baseline was never the documented one."""
    phase1 = _load("phase1_1d")
    assert phase1.BASE.exit_reversal == pytest.approx(0.015)
    assert phase1.BASE.exit_thresh_by_slug["default_5m"] == pytest.approx(0.08)
    assert phase1.BASE.offset == pytest.approx(0.02)
    assert phase1.BASE.queue_gate == pytest.approx(0.0)
    assert phase1.BASE.pair_cost_gate == pytest.approx(1.05)
    assert phase1.BASE.fill_model == "tape"


# ---------------------------------------------------------------------------
# 3. Worker cache loading
# ---------------------------------------------------------------------------

def test_pool_workers_use_the_memoised_cache_getter():
    """`load_cache()` re-unpickles ~336MB per task; `pool.map` dispatches one
    task per shard per config."""
    offenders = []
    for p in SWEEPS.glob("phase*.py"):
        src = p.read_text(encoding="utf-8")
        if "def _worker" in src and "load_cache()" in src:
            offenders.append(p.name)
    for name in ("validate_top.py",):
        src = (SWEEPS / name).read_text(encoding="utf-8")
        if "def _worker" in src and "load_cache()" in src:
            offenders.append(name)
    assert offenders == [], f"worker re-unpickles the cache in {offenders}"
