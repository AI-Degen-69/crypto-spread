# Task Plan — Issues #222 + #223: Dead-zone unit & unpaired-leg-at-expiry measurements

**Size tier:** Standard — one new research module + one test file + two artifacts + two
doc updates; both engines untouched. The single architectural decision (settlement
proxy convention) is already settled by `audit_settlement.py` precedent.
**Task type:** Research + Code (measurement lab; verification by automated test + one
reproducible run producing the artifacts).

## Context

- #222 asks whether the dead zone (`backtest/engine.py:BacktestParams.dead_zone_unit`,
  default `"pct"` @ 0.10) should be percent-of-window or absolute seconds. The knob
  ships with both units precisely so this can be measured (rule 8, "deliberately open").
- #223 asks whether an unpaired leg reaching the dead zone should be closed at the book
  (`naked_leg_at_expiry="close"`, default) or held to settlement (rule 14, "deliberately
  default close pending measurement").
- Both are answered from the same captured tick history (`run/ticks/`, 5 days,
  ~1.29M rows, 5m + 15m). The repo's established pattern for this is a read-only
  research script over the `ev_lab` window cache (`verify_205_fill_rate.py`,
  `audit_settlement.py`) — the lab follows it.
- Fill decisions reuse `book_math.resting_bid_filled` (one rule, #226/ADR-0002) with
  the sell-print pre-filter (#182); anchoring follows `sim2` (first valid mid inside
  `quote_range`, quotes at `mid - offset`, `newly_placed` on the placement tick).
- Settlement is proxied from the window's **final** two-sided mid (`> 0.5` → up won;
  ambiguity band `0.48 ≤ m ≤ 0.52` inclusive — e.g. 0.505 is ambiguous, counted not
  guessed), resolved after the walk completes; the same convention
  `audit_settlement.py` uses. The #223 valuation point (leg mid/bid for close) is the
  held leg's book at the first dead-zone tick.

## Tasks

- [x] **TASK-1 [Research/Core]**: Pure measurement logic module
  - Target files: `research/sweeps/dead_zone_lab.py`
  - What is built: pure, unit-testable functions — fill-timeline detection
    (time-to-first-fill / time-to-pair from quote placement, per leg), dead-zone
    boundary helpers for both units (`is_in_dead_zone` semantics from
    `strategy/book_math.py`), remaining-time volatility buckets, dead-zone-mid
    bucketing (0.05 wide), settlement proxy, and close-vs-hold value arithmetic
    (close: best bid − entry − taker fee; hold: settlement − entry), plus dataset
    staging helpers that reuse `ev_lab.build_cache/load_cache` (scratch cache pattern
    from `verify_205_fill_rate.py`).
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_dead_zone_lab.py -q`

- [x] **TASK-2 [QA/Tests]**: Targeted tests on synthetic windows
  - Target files: `tests/test_dead_zone_lab.py`
  - What is built: synthetic `Win`-shaped windows covering — fill before dead zone vs
    inside it; time-to-pair constant vs scaling fixture; pct vs sec boundary equality
    (e.g. 10% of 300s == 30s sec-unit); bucket edges (0.25 exactly, bucket with n < 30
    excluded from verdicts); settlement proxy (clear win, clear loss, ambiguous mid);
    close-vs-hold arithmetic incl. taker fee; `None`-book exclusion counters. Write
    tests first (TDD), then make TASK-1 pass them.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_dead_zone_lab.py -q`

- [x] **TASK-3 [Research/Measurement]**: Run the measurement, produce both artifacts
  - Target files: `research/sweeps/dead_zone_222.json`, `research/sweeps/naked_leg_223.json`
  - What is built: CLI (`python -m research.sweeps.dead_zone_lab [datasets...]`) that
    builds the per-window base table in one pass over the cache and emits:
    - #222: time-to-fill / time-to-pair distributions per duration; volatility per
      remaining-time bucket per duration; outcome of windows entered inside each
      candidate dead zone (pct grid × sec grid); machine-readable verdict + rationale.
    - #223: per-bucket settlement rate vs bucket price; best bid vs mid gap in the
      dead zone; close-vs-hold realised value distributions (mean/median/std/quartiles)
      per bucket; verdict per decision rules in the issue.
  - Helper skill: `source-driven-development`
  - Verify: run the CLI on `run/ticks/ticks_*.jsonl`; confirm both JSONs are written,
    stamped with dataset files + row counts, and finish within the 120s ceiling.

- [x] **TASK-4 [Docs]**: Report + decision-rule verdicts
  - Target files: `docs/dead-zone-naked-leg-measurements.md`,
    `docs/engine-decision-rules.md` (§8 and §14 only)
  - What is built: the measurement report (method, tables, verdicts, caveats: 5-day
    span, settlement proxy, bucket sample sizes), and the two rule sections cite the
    measured verdict with a link. Defaults do not change here.
  - Helper skill: `documentation-and-adrs`
  - Verify: review the doc against the JSON artifacts; figures in the doc must match
    the artifacts.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | `python -m pytest tests/test_dead_zone_lab.py -q` |
| TASK-2 | `python -m pytest tests/test_dead_zone_lab.py -q` (written first) |
| TASK-3 | `python -m research.sweeps.dead_zone_lab` → both JSONs + runtime check |
| TASK-4 | doc-vs-artifact figure review |

## Post-build gates (Station IV)
- `python -m pytest tests/test_dead_zone_lab.py tests/test_ev_sweep_lab.py tests/test_backtest_engine.py -q` — all green (lab + engine untouched parity guard).
- No changes under `backtest/`, `strategy/`, `server/` — verified by `git diff --stat`.
