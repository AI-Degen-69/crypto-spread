"""Tests for the golden dataset research cut (issue #312).

The research cut is a deterministic, stratified, floor-respecting subset of
`run/ticks/golden/`. These tests pin: determinism, floors at the multiplier,
representation (all series, all days), golden-dir immutability, byte-identical
raw lines, manifest provenance, and replay parity.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_research_cut import (  # noqa: E402
    MANIFEST_NAME,
    build_research_cut,
)
from scripts.verify_tick_data import verify_ticks_dir  # noqa: E402
from tests.test_verify_tick_data import make_sample_tick  # noqa: E402


def _tick(cid: str, series: str, ts: float, start: float, duration: int,
          mid: float = 0.50) -> dict:
    """One valid snapshot with a two-sided mid so _classify sees a real path.

    Both legs' books are built around the SAME mid: up leg at `mid`, down leg
    mirrored at `1 - mid` — the real market's geometry. two_sided_mid needs
    both legs priced or it returns None (the honest answer for one-sided).
    """
    up_bid = round(min(0.99, max(0.01, mid - 0.01)), 2)
    up_ask = round(up_bid + 0.02, 2)
    down_mid = round(1.0 - mid, 4)
    down_bid = round(min(0.99, max(0.01, down_mid - 0.01)), 2)
    down_ask = round(down_bid + 0.02, 2)
    t = make_sample_tick(
        cid=cid, series=series, ts=ts, start_ts=start,
        end_ts=start + duration, duration=duration, mid=mid,
        best_bid=up_bid, best_ask=up_ask,
    )
    t["down_book"] = {
        "bids": {str(down_bid): 100.0}, "asks": {str(down_ask): 100.0},
        "best_bid": down_bid, "best_ask": down_ask, "malformed": 0,
    }
    return t


def _write_rich_golden(directory: Path, *, days: int = 5, markets: int = 10,
                       windows_per_market: int = 3, sparse: bool = False) -> dict[str, float]:
    """A synthetic golden set that looks like the real one: every (market, day)
    cell gets windows whose price paths span all three classes.

    sparse=True writes 3 snaps per window instead of a full 5s grid. Tests that
    never invoke the real verifier (whose gap checks need the dense cadence)
    use it to keep the fixture tiny and the whole file fast — the stratifier,
    the allocator, and the replay parity gate only care about window-level
    structure, not tick density.
    """
    import hashlib

    base = 1789296000.0  # 2026-09-13T00:00:00Z, day-aligned
    day_len = 86400.0
    series_list = [
        f"{asset}-up-or-down-{duration // 60}m"
        for asset, duration in [("btc", 300), ("eth", 300), ("bnb", 300),
                                ("sol", 300), ("xrp", 300), ("btc", 900),
                                ("eth", 900), ("bnb", 900), ("sol", 900),
                                ("xrp", 900)][:markets]
    ]
    hashes: dict[str, float] = {}
    directory.mkdir(parents=True, exist_ok=True)
    for d in range(days):
        day_start = base + d * day_len
        lines: list[str] = []
        for s_i, series in enumerate(series_list):
            duration = 900 if series.endswith("15m") else 300
            for w in range(windows_per_market):
                # Deterministic per-window class: cycle flat/monotonic/oscillating.
                win_start = day_start + w * (duration + 60)
                cid = f"0xD{d}S{s_i}W{w:02d}"
                if w % 3 == 0:      # flat: mid never leaves 0.50 ± 0.01
                    mids = [0.50, 0.505, 0.50]
                elif w % 3 == 1:    # monotonic: one side moves >= 0.02
                    mids = [0.50, 0.58, 0.60]
                else:               # oscillating: both sides >= 0.02
                    mids = [0.50, 0.60, 0.48, 0.58, 0.47]
                n = len(mids)
                if sparse:
                    # One snap per distinct mid, spread across the window —
                    # every class is preserved exactly (the mid path is what
                    # _classify sees), at a fraction of the lines of the
                    # dense grid. Enough for classification, allocation, and
                    # replay parity — NOT for the verifier's gap checks
                    # (those tests use dense fixtures).
                    grid_offsets = [round(i * duration / (n - 1))
                                    for i in range(n)]
                else:
                    # Tick cadence: 5s steps for BOTH durations — under the
                    # verifier's 6s gap threshold (max_gap_sec=6) so the fixture
                    # itself passes PASS / COMPLETE CAPTURE with zero gaps, and
                    # the last tick lands exactly at end_ts (no early cutoff).
                    step = 5
                    grid_offsets = list(range(0, duration, step)) + [duration]
                for k in grid_offsets:
                    # Position along the mid path by fraction of the window
                    # (k is in seconds; the last offset k=duration is the close).
                    frac = k / duration
                    idx = min(n - 1, int(frac * (n - 1) + 0.5))
                    m = mids[idx]
                    ts = win_start + k
                    lines.append(json.dumps(
                        _tick(cid, series, ts, win_start, duration, mid=m)))
        path = directory / f"ticks_2026-09-{13 + d}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        hashes[path.name] = _fake_sha(path)
    return hashes


def _fake_sha(path: Path) -> float:
    import hashlib
    return int(hashlib.sha256(path.read_bytes()).hexdigest()[:12], 16) / 1e12


def test_cut_deterministic_and_floor_respecting(tmp_path):
    """Same inputs → byte-identical cut; floors hold at the multiplier."""
    golden = tmp_path / "golden"
    _write_rich_golden(golden, days=5, windows_per_market=60, sparse=True)
    hashes_before = {p.name: p.stat().st_size for p in golden.glob("*.jsonl")}

    cut1 = tmp_path / "cut1"
    cut2 = tmp_path / "cut2"
    m1 = build_research_cut(golden, cut1, multiplier=4, seed=0)
    m2 = build_research_cut(golden, cut2, multiplier=4, seed=0)

    assert m1["totals"]["windows"] == m2["totals"]["windows"]
    for p in sorted(cut1.glob("*.jsonl")):
        assert p.read_bytes() == (cut2 / p.name).read_bytes(), p.name
    assert (cut1 / MANIFEST_NAME).read_bytes() == (cut2 / MANIFEST_NAME).read_bytes()

    # Floors at M=4: >=2000 windows, >=200 per market-duration pair, >=4 days.
    assert m1["totals"]["windows"] >= 2000
    assert m1["totals"]["min_windows_per_market"] >= 200
    assert m1["totals"]["days_represented"] >= 4
    assert m1["totals"]["series_represented"] == 10

    # Golden dir untouched.
    hashes_after = {p.name: p.stat().st_size for p in golden.glob("*.jsonl")}
    assert hashes_before == hashes_after


def test_cut_preserves_window_class_mix_per_market(tmp_path):
    """Stratification keeps all three window classes present per market."""
    golden = tmp_path / "golden"
    _write_rich_golden(golden, days=5, windows_per_market=60, sparse=True)
    cut = tmp_path / "cut"
    m = build_research_cut(golden, cut, multiplier=4, seed=0)

    classes = m["totals"]["classes"]
    assert set(["flat", "monotonic", "oscillating"]) <= set(classes)
    assert all(v > 0 for v in classes.values())


def test_cut_lines_are_byte_identical_to_golden(tmp_path):
    """Every cut line exists verbatim in the golden set (raw-line copy)."""
    golden = tmp_path / "golden"
    _write_rich_golden(golden, days=5, windows_per_market=60, sparse=True)
    golden_lines = set()
    for p in golden.glob("*.jsonl"):
        golden_lines.update(p.read_text(encoding="utf-8").splitlines())

    cut = tmp_path / "cut"
    build_research_cut(golden, cut, multiplier=4, seed=0)
    cut_lines = []
    for p in sorted(cut.glob("*.jsonl")):
        cut_lines.extend(p.read_text(encoding="utf-8").splitlines())
    assert cut_lines
    assert all(line in golden_lines for line in cut_lines)


def test_cut_passes_verify_research_ready(tmp_path):
    """The cut independently reaches RESEARCH_READY via the real verifier.

    Dense cadence + M=1: the verifier needs the 5s tick grid (gap checks),
    and RESEARCH_READY at M=1 (≥500 windows, ≥50/market) proves the same
    readiness logic.    The fixture sits at the policy minimum — 3 days
    (min time_blocks) × 17 windows/market/day (51/market, just over the M=1
    floor of 50) — so the dense grid costs as little as it can.
    """
    golden = tmp_path / "golden"
    _write_rich_golden(golden, days=3, windows_per_market=17)
    cut = tmp_path / "cut"
    build_research_cut(golden, cut, multiplier=1, seed=0)

    rep = verify_ticks_dir(cut)
    assert rep["readiness"]["level"] == "RESEARCH_READY"
    # Index sidecars: the cut is SMALL by design, so build_index is cheap —
    # assert the sidecars materialize on demand (same contract as before,
    # without making the build itself pay for an eager full re-scan).
    from backtest.index import build_index
    for p in cut.glob("*.jsonl"):
        build_index(p)
        assert p.with_suffix(p.suffix + ".idx").exists()


def test_guardrail_replay_parity(tmp_path):
    """Replaying the sampled windows from the cut equals replaying them from
    the golden set: same bytes in, identical WindowResult out."""
    from backtest.engine import BacktestParams

    golden = tmp_path / "golden"
    _write_rich_golden(golden, days=5, windows_per_market=60, sparse=True)
    cut = tmp_path / "cut"
    m = build_research_cut(golden, cut, multiplier=4, seed=0)
    assert m["guardrail"]["passed"] is True
    assert m["guardrail"]["golden_untouched"] is True
    assert m["guardrail"]["windows_compared"] > 0

    params = BacktestParams()
    # Replay ONLY the selected windows from the golden side for comparison.
    from backtest.engine import group_by_cid, iter_ticks
    selected_cids = set(m["selection"]["selected_cids"])
    golden_groups = {cid: snaps for cid, snaps in group_by_cid(list(iter_ticks(golden)))
                     if cid in selected_cids}
    golden_results = {}
    from backtest.engine import _simulate_window
    for cid, snaps in golden_groups.items():
        r = _simulate_window(snaps, params)
        golden_results[cid] = (r.pair_captured, r.exit_taken, r.pnl_cents,
                               r.filled_up, r.filled_down, r.class_label)
    cut_results = {}
    for cid, snaps in group_by_cid(list(iter_ticks(cut))):
        r = _simulate_window(snaps, params)
        cut_results[cid] = (r.pair_captured, r.exit_taken, r.pnl_cents,
                            r.filled_up, r.filled_down, r.class_label)
    assert golden_results == cut_results
    assert set(cut_results) == selected_cids


def test_sparse_day_no_phantom_allocation(tmp_path):
    """A pair missing on one day (like the real 09-18 with no 15m windows)
    must not create phantom allocation: every allocated window is selectable.

    Regression: the old rescue invented (pair, day, class) cells with no
    windows behind them, so allocated counts never materialized and the
    per-pair floor failed on real data while synthetic fixtures stayed green.
    """
    golden = tmp_path / "golden"
    # wpm=17: 15m pairs hold 3×17=51 windows (one day stripped) — just above
    # the M=1 floor of 50, mirroring the real bnb-15m squeeze (159 vs 150).
    _write_rich_golden(golden, days=4, windows_per_market=17, sparse=True)
    # Drop every 15m line from one day file — that pair-day has no cells.
    victim = golden / "ticks_2026-09-14.jsonl"
    kept = [ln for ln in victim.read_text(encoding="utf-8").splitlines()
            if '"duration": 300' in ln or '"duration":300' in ln
            or json.loads(ln).get("duration") == 300]
    victim.write_text("\n".join(kept) + "\n", encoding="utf-8")

    cut = tmp_path / "cut"
    m = build_research_cut(golden, cut, multiplier=1, seed=0)
    assert m["guardrail"]["passed"] is True
    # Allocation is fully realizable: no phantom cells.
    assert sum(m["selection"]["cells"].values()) == len(m["selection"]["selected_cids"])
    assert m["totals"]["min_windows_per_market"] >= 50


def test_parity_gate_reads_emitted_bytes(tmp_path):
    """The in-script parity gate must fail on corrupted emission.

    Regression: the first gate compared in-memory groups to themselves, so it
    passed even with broken output. Corrupt one emitted line and the gate must
    report (False, <full count>).
    """
    from scripts.build_research_cut import _replay_parity_from_disk
    from backtest.engine import BacktestParams, group_by_cid, iter_ticks

    golden = tmp_path / "golden"
    _write_rich_golden(golden, days=3, windows_per_market=17, sparse=True)
    cut = tmp_path / "cut"
    m = build_research_cut(golden, cut, multiplier=1, seed=0)
    assert m["guardrail"]["passed"] is True

    golden_groups = {cid: snaps
                     for cid, snaps in group_by_cid(list(iter_ticks(golden)))}
    golden_meta = {cid: {"snaps": snaps}
                   for cid, snaps in golden_groups.items()}
    params = BacktestParams()
    ok, n = _replay_parity_from_disk(
        golden_meta, cut, m["selection"]["selected_cids"], params)
    assert ok is True and n == len(m["selection"]["selected_cids"])

    # Drop a whole window from the emitted bytes: its snaps move to a
    # phantom cid, so the disk re-read misses it (one missing snap alone
    # can still replay identically — only a missing window must fail).
    victim = sorted(cut.glob("*.jsonl"))[0]
    lines = victim.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 1
    victim_cid = json.loads(lines[0])["cid"]
    moved = 0
    for i, ln in enumerate(lines):
        obj = json.loads(ln)
        if obj.get("cid") == victim_cid:
            obj["cid"] = "0xCORRUPTED"
            lines[i] = json.dumps(obj)
            moved += 1
    assert moved > 1
    victim.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok2, n2 = _replay_parity_from_disk(
        golden_meta, cut, m["selection"]["selected_cids"], params)
    assert ok2 is False and n2 < len(m["selection"]["selected_cids"])


def test_out_inside_golden_refused(tmp_path):
    """--out at (or inside) the golden dir fails loudly before any delete."""
    from scripts.build_research_cut import build_research_cut as build

    golden = tmp_path / "golden"
    _write_rich_golden(golden, days=1, windows_per_market=1, sparse=True)
    with pytest.raises(ValueError, match="read-only"):
        build(golden, golden, multiplier=1, seed=0)
    with pytest.raises(ValueError, match="read-only"):
        build(golden, golden / "sub", multiplier=1, seed=0)
    # Golden files survived the refusal.
    assert len(list(golden.glob("*.jsonl"))) == 1


def test_missing_golden_dir_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_research_cut(tmp_path / "nope", tmp_path / "cut")
