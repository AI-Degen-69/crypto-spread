"""Regression tests for the EV sweep lab (issue #182).

The lab is the code that produced `patient_band_maker`, and the next
collection run replays it against fresh ticks. These tests pin the defects
found on PR #181 so a rerun cannot silently reintroduce them.

Everything here runs without `run/sweeps/window_cache.pkl` — that cache is
derived from `run/ticks/`, both are gitignored, and neither exists on a clean
checkout or in CI.
"""
import contextlib
import importlib.util
import json
import sys
import weakref
from pathlib import Path
from unittest import mock

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


# ---------------------------------------------------------------------------
# 13. build_cache streams instead of buffering a whole day
# ---------------------------------------------------------------------------

def _snap(cid, ts, *, up_tok="U", dn_tok="D", mid=0.5, up_bid=0.47, up_ask=0.53,
          dn_bid=0.46, dn_ask=0.52, prints=()):
    """One collector tick line for `cid`, in the on-disk shape."""
    return {
        "cid": cid, "series": "eth-up-or-down-5m", "slug": f"{cid}-slug",
        "duration": 300, "start_ts": 1000.0, "ts": ts, "mid": mid,
        "up_token": up_tok, "down_token": dn_tok,
        "up_book": {"token_id": up_tok, "best_bid": up_bid, "best_ask": up_ask,
                    "bids": {"0.47": 120.0, "0.40": 55.0, "0.01": 9.0}},
        "down_book": {"token_id": dn_tok, "best_bid": dn_bid, "best_ask": dn_ask,
                      "bids": {"0.46": 80.0}},
        "tape_delta": [{"asset": a, "price": p, "size": s} for a, p, s in prints],
    }


def _write_ticks(tmp_path, day, snaps):
    d = tmp_path / "ticks"
    d.mkdir(exist_ok=True)
    p = d / f"ticks_{day}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for s in snaps:
            f.write(json.dumps(s) + "\n")
    return d


@contextlib.contextmanager
def _cache_dirs(tmp_path, ticks_dir):
    """Point the lab's module-level paths at a scratch tree for one build."""
    old_ticks, old_cache = ev_lab.TICKS_DIR, ev_lab.CACHE_PATH
    ev_lab.TICKS_DIR = ticks_dir
    ev_lab.CACHE_PATH = tmp_path / "sweeps" / "window_cache.pkl"
    try:
        yield ev_lab.CACHE_PATH
    finally:
        ev_lab.TICKS_DIR, ev_lab.CACHE_PATH = old_ticks, old_cache


def test_build_cache_does_not_hold_a_whole_day_of_parsed_snapshots(tmp_path):
    """The defect this pins: the old two-pass shape parsed an entire day into
    `raw[cid] -> [snapshot dicts]` before compacting any of it.

    Measured on the real 490MB tick file that cost **3.34GB of peak RSS** —
    5.7x the file — so a 1GB day needed ~7GB of headroom that a 16GB machine
    running the collector, the dashboard and a browser does not have. The
    streamed shape measured 0.59GB on the same file.

    Peak RSS is not assertable in CI, so the invariant is tested directly:
    how many parsed snapshots are alive at once. It must not grow with the
    number of snapshots in the file.
    """
    n_snaps = 200
    snaps = [_snap(f"c{i % 4}", 1000.0 + i) for i in range(n_snaps)]
    ticks = _write_ticks(tmp_path, "2026-09-13", snaps)

    class _Tracked(dict):
        """A dict that can be weakly referenced, so liveness is observable.

        `dict` sets `__hash__` to None, and an unhashable value cannot enter a
        WeakSet — the resulting TypeError lands inside `build_cache`'s
        `except Exception: continue`, which skips every line and yields an
        empty cache rather than a failure. Identity hashing is also the right
        semantics here: liveness is per object, not per value.
        """

        __hash__ = object.__hash__

    live = weakref.WeakSet()
    high_water = []
    real_loads = ev_lab.json.loads
    real_compact = ev_lab._compact_bids

    def loads(s, *a, **kw):
        obj = _Tracked(real_loads(s, *a, **kw))
        live.add(obj)
        return obj

    def compact(d):
        # Sampled once per book, i.e. twice per snapshot, mid-build.
        high_water.append(len(live))
        return real_compact(d)

    with mock.patch.object(ev_lab.json, "loads", loads), \
            mock.patch.object(ev_lab, "_compact_bids", compact), \
            _cache_dirs(tmp_path, ticks):
        ev_lab.build_cache(force=True)

    assert high_water, "the build never parsed a snapshot"
    # A streaming build drops each snapshot before reading the next, so only a
    # handful are ever alive. The buffering shape reached `n_snaps`.
    assert max(high_water) < n_snaps // 4, (
        f"up to {max(high_water)} of {n_snaps} parsed snapshots were alive at "
        "once; build_cache is buffering the day again")


def test_build_cache_output_survives_the_streaming_rewrite(tmp_path):
    """Every field the sweep reads, on a file with interleaved windows."""
    snaps = [
        _snap("a", 1000.0, prints=[("U", 0.53, 7.0), ("D", 0.30, 2.0)]),
        _snap("b", 1000.5),
        _snap("a", 1001.0, prints=[("U", 0.47, 3.0)]),
        _snap("b", 1001.5),
    ]
    ticks = _write_ticks(tmp_path, "2026-09-13", snaps)
    with _cache_dirs(tmp_path, ticks):
        ev_lab.build_cache(force=True)
        wins = ev_lab.load_cache()

    assert [w.cid for w in wins] == ["a", "b"], "first-appearance order changed"
    a, b = wins
    assert a.day == "2026-09-13" and a.series == "eth-up-or-down-5m"
    assert a.duration == 300 and a.start_ts == 1000.0
    assert a.up_token == "U" and a.dn_token == "D"
    # Parallel arrays stay aligned and carry only this cid's snapshots.
    assert a.ts == [1000.0, 1001.0] and b.ts == [1000.5, 1001.5]
    assert a.s_mid == [0.5, 0.5]
    assert a.up_bb == [0.47, 0.47] and a.up_ba == [0.53, 0.53]
    assert a.dn_bb == [0.46, 0.46] and a.dn_ba == [0.52, 0.52]
    # `_compact_bids` drops levels below BOOK_MIN_PX and sorts descending.
    assert list(a.up_bids[0]) == [0.47, 120.0, 0.40, 55.0]
    # Prints route to the leg that owns the token, and carry a real side:
    # 0.53 lifts the ask (buy), 0.47 hits the bid (sell).
    up0, dn0 = a.tape[0]
    assert up0 == [(0.53, 7.0, ev_lab.SIDE_BUY)]
    assert dn0 == [(0.30, 2.0, ev_lab.SIDE_SELL)]
    assert a.tape[1][0] == [(0.47, 3.0, ev_lab.SIDE_SELL)]
    assert b.tape == [([], []), ([], [])]


def test_build_cache_rejects_a_tick_file_that_is_not_per_cid_ascending(tmp_path):
    """Streaming appends in file order, so it relies on per-cid ascending ts.

    That holds today (0 regressions over 503,752 real snapshots) because the
    collector appends on one thread. If it ever stops holding, the parallel
    arrays would be silently misaligned and every simulation would read the
    wrong book for a timestamp — so the build has to fail loudly instead.
    """
    snaps = [_snap("a", 1000.0), _snap("a", 999.0)]
    ticks = _write_ticks(tmp_path, "2026-09-13", snaps)
    with _cache_dirs(tmp_path, ticks):
        with pytest.raises(ValueError, match="went backwards"):
            ev_lab.build_cache(force=True)


# ---------------------------------------------------------------------------
# 14. sim2 rejects the knobs it silently ignored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"stop_loss_enabled": False},
    {"exit_thresh_naked": 0.03},
    {"naked_leg_timeout_pct": 0.5},
    {"enable_leg_chase": True},
    {"max_start_delay_sec": 5.0},
])
def test_sim2_rejects_knobs_no_research_simulator_implements(kwargs):
    """Issue #164 put four knobs on `BacktestParams` that `sim2` never reads.

    Measured before the guard: flipping any of them left `sim2`'s output
    identical across 400 real windows. `patient_band_maker` *is*
    `stop_loss_enabled=False`, so a sweep configured that way would have
    reported numbers for a strategy with a stop loss still armed — exactly the
    defect #182 fixed for `entry_delay_sec`/`entry_band`, reopened by #164.
    `max_start_delay_sec` is older and has the same gap (`engine.py:1083`).
    """
    from backtest.engine import BacktestParams
    with pytest.raises(ValueError, match="sim2 ignores"):
        ev_lab.reject_knobs_sim2_ignores(BacktestParams(**kwargs))


def test_sim2_rejects_its_own_arguments_set_on_the_params_instead():
    """`sim2` takes these as arguments and never reads the fields of the same
    name, so setting them on the params is as silent as not implementing them."""
    from backtest.engine import BacktestParams
    for kwargs in ({"entry_delay_sec": 60.0}, {"entry_band": 0.04}):
        with pytest.raises(ValueError, match="sim2 ignores"):
            ev_lab.reject_knobs_sim2_ignores(BacktestParams(**kwargs))


def test_sim2_itself_enforces_the_guard():
    """Asserting on the helper alone would pass even if nothing called it."""
    sim2 = _load("sim2").sim2
    from backtest.engine import BacktestParams
    w = _one_tick_window()
    with pytest.raises(ValueError, match="sim2 ignores"):
        sim2(w, BacktestParams(stop_loss_enabled=False))
    # The supported path is untouched: defaults simulate, and the two research
    # knobs still work when passed as arguments.
    assert isinstance(sim2(w, BacktestParams()), dict)
    assert isinstance(sim2(w, BacktestParams(), entry_delay_sec=60.0,
                           entry_band=0.04), dict)


def test_the_guard_compares_against_defaults_not_truthiness():
    """`stop_loss_enabled` defaults to True, so `if getattr(p, k)` — the shape
    the guard had — would reject every ordinary config and wave through
    `stop_loss_enabled=False`, the one value that changes the simulation."""
    from backtest.engine import BacktestParams
    ev_lab.reject_knobs_sim2_ignores(BacktestParams(stop_loss_enabled=True))
    ev_lab._reject_unsupported_knobs(BacktestParams(stop_loss_enabled=True))
    with pytest.raises(ValueError):
        ev_lab.reject_knobs_sim2_ignores(BacktestParams(stop_loss_enabled=False))


def test_no_backtest_param_is_silently_unread_by_the_research_simulators():
    """Exhaustiveness: a knob added to `BacktestParams` must either be read
    here or be declared unsupported. Otherwise the next #164 repeats this."""
    import dataclasses
    import re
    from backtest.engine import BacktestParams

    src = ((SWEEPS / "sim2.py").read_text(encoding="utf-8")
           + (SWEEPS / "ev_lab.py").read_text(encoding="utf-8"))
    exempt = {
        # Read through `p.exit_thresh(slug, duration, series=...)`, not by name.
        "exit_thresh_by_slug",
        # Declared in the engine's registry but applied nowhere, engine
        # included — so it is not a research/engine divergence to guard.
        "min_quote_shares",
    }
    unread = [
        f.name for f in dataclasses.fields(BacktestParams)
        if f.name not in ev_lab.UNSUPPORTED_KNOBS and f.name not in exempt
        and not re.search(rf"\bp\.{f.name}\b", src)
    ]
    assert not unread, (
        f"{unread} are on BacktestParams but read by neither research "
        "simulator and not listed in UNSUPPORTED_KNOBS; a sweep setting one "
        "would report numbers for a configuration it never ran")


def test_no_sweep_driver_puts_a_guarded_knob_on_its_params(tmp_path):
    """The guard must not break the drivers it protects.

    Only the knob reaching a `BacktestParams` is fatal. Every driver today
    routes `entry_delay_sec`/`entry_band` through a task dict and unpacks them
    into `sim2(...)` arguments — `phase4_universe.py:75` does exactly that, and
    a text search cannot tell it apart from the dangerous form. So this reads
    the syntax instead: the keywords of every `BacktestParams(...)` and
    `replace(...)` call, and the keys of any dict literal bound to a name
    ending in `params_kwargs`.
    """
    import ast

    guarded = set(ev_lab.UNSUPPORTED_KNOBS)
    offenders = []
    for path in sorted(SWEEPS.glob("*.py")):
        if path.name in ("ev_lab.py", "sim2.py"):
            continue  # they define the guard rather than call it
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name in ("BacktestParams", "replace"):
                    for kw in node.keywords:
                        if kw.arg in guarded:
                            offenders.append(f"{path.name}:{node.lineno} "
                                             f"{name}(..., {kw.arg}=...)")
            elif isinstance(node, ast.Assign):
                if not isinstance(node.value, ast.Dict):
                    continue
                targets = [getattr(t, "id", "") or getattr(t, "attr", "")
                           for t in node.targets]
                if not any(str(t).endswith("params_kwargs") for t in targets):
                    continue
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and k.value in guarded:
                        offenders.append(f"{path.name}:{node.lineno} "
                                         f"params_kwargs[{k.value!r}]")
    assert offenders == [], (
        "these drivers set a knob the research simulators ignore directly on "
        f"the params; the guard rejects them at run time: {offenders}")


# ---------------------------------------------------------------------------
# 15. Sweeping on a machine that cannot hold eight copies of the cache
# ---------------------------------------------------------------------------

def _tiny_cache(n=4):
    """`n` minimal windows, enough for `fast_simulate` to return a row each."""
    from array import array
    book = array("d", [0.48, 100.0])
    return [ev_lab.Win({
        "cid": f"0x{i}", "series": "eth-up-or-down-5m",
        "slug": "eth-up-or-down-5m", "duration": 300,
        "start_ts": 1_760_000_000.0, "day": "2026-09-11",
        "ts": [1_760_000_000.0], "s_mid": [0.50],
        "up_bb": [0.49], "up_ba": [0.51], "dn_bb": [0.49], "dn_ba": [0.51],
        "up_bids": [book], "dn_bids": [book], "tape": [([], [])],
        "up_token": "tok_up", "dn_token": "tok_dn",
    }) for i in range(n)]


@contextlib.contextmanager
def _installed_cache(windows):
    """Put `windows` behind `_get_cache()` without touching run/sweeps."""
    old = ev_lab._CACHE
    ev_lab._CACHE = windows
    try:
        yield
    finally:
        ev_lab._CACHE = old


def test_sweep_configs_serial_path_resolves_window_positions(tmp_path):
    """The `workers <= 1` branch was broken for as long as it existed.

    `tasks` carries positions into the cache, because the parallel branch ships
    indices to workers that hold their own copy. The serial branch passed that
    index list straight to `run_config_on`, which raised `'int' object has no
    attribute 'duration'`. Nothing hit it while the drivers all requested 6-8
    workers; it surfaced the moment a machine could only afford one.
    """
    cache = _tiny_cache()
    with _installed_cache(cache):
        out = ev_lab.sweep_configs(
            [{"name": "cfg-a", "params_kwargs": {"offset": 0.02}},
             {"name": "cfg-b", "params_kwargs": {"offset": 0.03}}],
            workers=1, n_boot=50, size=5)
    assert [r["name"] for r in out] == ["cfg-a", "cfg-b"]
    assert all(r["n"] == len(cache) for r in out), \
        "every window should reach the simulator"


def test_sweep_configs_serial_path_honours_filters(tmp_path):
    """Resolving positions must select the filtered windows, not the first N."""
    cache = _tiny_cache(4)
    cache[0].series = cache[1].series = "xrp-up-or-down-15m"
    with _installed_cache(cache):
        out = ev_lab.sweep_configs(
            [{"name": "only-xrp", "params_kwargs": {},
              "series_filter": ["xrp-up-or-down-15m"]}],
            workers=1, n_boot=50, size=5)
    assert out[0]["n"] == 2, "the series filter was dropped by the serial path"


@contextlib.contextmanager
def _sized_cache(tmp_path, mb: float):
    """Point `CACHE_PATH` at a real file of a known size.

    Without this the sizing arithmetic is never reached on a machine that has
    no `run/sweeps/window_cache.pkl` — `CACHE_PATH.stat()` raises and the
    function returns its fallback instead. That is every clean checkout and all
    of CI, where the cache is gitignored and derived. Tests that assert on the
    arithmetic have to supply a cache rather than assume one.
    """
    p = tmp_path / "window_cache.pkl"
    p.write_bytes(b"\0" * int(mb * 1024 * 1024))
    old = ev_lab.CACHE_PATH
    ev_lab.CACHE_PATH = p
    try:
        yield p
    finally:
        ev_lab.CACHE_PATH = old


@contextlib.contextmanager
def _sized_cache(tmp_path, mb: float):
    """Point `CACHE_PATH` at a real file of a known size.

    Without this the sizing arithmetic is never reached on a machine with no
    `run/sweeps/window_cache.pkl` — `CACHE_PATH.stat()` raises and the function
    returns its fallback instead. That is every clean checkout and all of CI,
    where the cache is gitignored and derived. A test asserting on the
    arithmetic has to supply a cache rather than assume one.
    """
    p = tmp_path / "window_cache.pkl"
    p.write_bytes(b"\0" * int(mb * 1024 * 1024))
    old = ev_lab.CACHE_PATH
    ev_lab.CACHE_PATH = p
    try:
        yield p
    finally:
        ev_lab.CACHE_PATH = old


def test_safe_worker_count_never_returns_less_than_one(tmp_path):
    """A pool of zero would deadlock; the floor is the serial path."""
    with _sized_cache(tmp_path, 8):
        assert ev_lab.safe_worker_count(
            8, reserve_bytes=float("inf"), available_bytes=64e9) == 1
        assert ev_lab.safe_worker_count(0, available_bytes=64e9) == 1
        assert ev_lab.safe_worker_count(-5, available_bytes=64e9) == 1


def test_safe_worker_count_never_exceeds_the_request(tmp_path):
    """It clamps down for memory; it must never invent workers."""
    with _sized_cache(tmp_path, 1):    # tiny cache: memory is not the limit
        assert ev_lab.safe_worker_count(
            4, reserve_bytes=0.0, available_bytes=64e9) == 4
        assert ev_lab.safe_worker_count(
            1, reserve_bytes=0.0, available_bytes=64e9) == 1


def test_safe_worker_count_clamps_to_what_memory_allows(tmp_path):
    """The arithmetic itself, on injected memory rather than this machine's.

    Injected because the first version of this test asserted on sizing that CI
    never reached — CI has neither `psutil` nor a window cache, so the function
    returned its fallback and the assertion passed locally for a reason that
    had nothing to do with what it claimed to check.
    """
    with _sized_cache(tmp_path, 1024):          # 1GB pickle -> ~2.87GB each
        per = 1024 * 1024 * 1024 * ev_lab.CACHE_RSS_FACTOR
        # Room for exactly three copies, and not a fourth.
        assert ev_lab.safe_worker_count(
            8, reserve_bytes=0.0, available_bytes=per * 3.5) == 3
        assert ev_lab.safe_worker_count(
            8, reserve_bytes=0.0, available_bytes=per * 0.9) == 1


def test_safe_worker_count_falls_back_low_when_memory_cannot_be_read(tmp_path):
    """`psutil` is absent from CI, so the probe has to be allowed to fail.

    Guessing high costs a thrashing machine and a sweep that may never finish;
    guessing low costs about 2.5s per config.
    """
    with _sized_cache(tmp_path, 8):
        with mock.patch.object(ev_lab, "available_memory_bytes",
                               return_value=None):
            assert ev_lab.safe_worker_count(8) == ev_lab.UNMEASURABLE_WORKERS


def test_available_memory_probe_survives_a_missing_psutil():
    """The probe reports "unknown" rather than raising into the caller."""
    import builtins
    real_import = builtins.__import__

    def no_psutil(name, *a, **kw):
        if name == "psutil":
            raise ImportError("not installed")
        return real_import(name, *a, **kw)

    with mock.patch.object(builtins, "__import__", no_psutil):
        assert ev_lab.available_memory_bytes() is None


def test_safe_worker_count_falls_back_low_when_the_cache_is_absent(tmp_path):
    """No cache means no way to size a worker: every clean checkout, and CI."""
    old = ev_lab.CACHE_PATH
    ev_lab.CACHE_PATH = tmp_path / "does_not_exist.pkl"
    try:
        assert ev_lab.safe_worker_count(
            8, available_bytes=64e9) == ev_lab.UNMEASURABLE_WORKERS
    finally:
        ev_lab.CACHE_PATH = old


def test_sweep_pool_runs_in_process_rather_than_spawning_one_worker():
    """`Pool(processes=1)` is the worst of both: the parent holds the cache and
    the single spawned worker loads a second copy, for no parallelism."""
    with mock.patch.object(ev_lab, "safe_worker_count", return_value=1):
        with ev_lab.sweep_pool(8) as pool:
            assert isinstance(pool, ev_lab._SerialPool)
            assert pool.map(lambda x: x * 2, [1, 2, 3]) == [2, 4, 6]


def test_no_sweep_driver_hardcodes_a_worker_count_again():
    """Each driver hardcoded 8 (or 5), sized for a far smaller dataset. Against
    the 2026-09-14 capture that is ~14GB of window cache across the workers."""
    import re
    offenders = []
    for path in sorted(SWEEPS.glob("*.py")):
        if path.name == "ev_lab.py":
            continue  # defines the helpers
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:processes|workers)\s*=\s*(\d+)", src):
            if int(m.group(1)) > 1:
                offenders.append(f"{path.name}:{src[:m.start()].count(chr(10)) + 1}")
    assert offenders == [], (
        "these drivers size their pool without asking how much memory exists: "
        f"{offenders} — use safe_worker_count()/sweep_pool()")
