# Plan: Issue #146 — Replay cross-check: official backtest of the shadow night vs paper results

Task Type: Research + Code
Size Tier: Small
Target Files: (no production code changes) new `scripts/replay_shadow_check.py`,
  new artifact dir `runs/paper/2026-09-11_22-10_IDT/replay_comparison/`,
  `tests/test_backtest_engine.py` (gate only, read-only)

Decisions locked with user: none — requirements fully clear from the issue;
`interview-me` was skipped. Two issue-text deviations resolved by local
evidence (no operator question needed): stale shadow path + partial tick
overlap (see §1).

## 1. Spec (embedded — Small tier, no SPEC.md change)

### Goal
Replay the 11h shadow night through the official backtest engine with the
winning config and quantify paper-vs-replay divergence, validating or
indicting the paper fill simulator before the live micro-pilot (#143).

### Verified ground truth (local, 2026-09-13)
- Shadow dir (issue cites stale `run/shadow_ev/...`; actual location):
  `runs/paper/2026-09-11_22-10_IDT/data/` — `meta.json` (config hypothesis),
  `final.json` (totals), `trades.jsonl` (66 events), `snapshots.jsonl`, log.
- Shadow ran 2026-09-11T22:10:54Z → 2026-09-12T09:10:58Z. Totals:
  realized +$6.74, 66 events, 53 pairs + 13 settles, win-rate 97%.
- Tick coverage starts 2026-09-12T00:00:02Z (`run/ticks/ticks_2026-09-12.jsonl`,
  full-depth `up_book`/`down_book` + `tape_delta` present — engine-ready).
  `ticks_2026-09-11.jsonl` does NOT exist locally.
- Overlap (measured, refined in build): 55/66 events (45 pairs + 10 settles,
  +$5.265) fall inside tick coverage AND open inside coverage
  (event epoch in [T0, T1], window start ≥ T0 − 1s); 11 events (+$1.475) are
  pre-coverage/boundary and cannot be fill-replayed (only legacy
  `run/observations/obs_2026-09-11.jsonl` exists — no depth, engine-incompatible).
- Blocker resolved: #145 knobs LANDED (`BacktestParams.entry_delay_sec`,
  `entry_band`, `backtest/engine.py:161-162`); `/api/backtest` exposes them
  (`server/osc_dash.py:534-535`).

### In scope
1. `python -m scripts.verify_tick_data run/ticks/ticks_2026-09-12.jsonl`
   integrity gate for the replayed range.
2. Exact-config replay via direct engine driver (NOT `/api/backtest`:
   the API cannot set `max_reentries_per_window=0`, which the shadow used;
   engine default is 1). The driver calls `backtest.engine._simulate_window`
   per included window — the same per-window core `/api/backtest` uses. Shadow→engine mapping (all other fields API- and
   engine-settable): offset 0.03, entry_delay_sec 60, entry_band 0.04,
   fill_model "tape", quote_shares 5, pair_cost_gate 0.98, exit 0.05/0.05,
   exit_reversal 0.5, entry_timeout_pct 1.0, max_start_elapsed_pct 0.1,
   reentry_drift_band 0.015, min_requote_remaining_sec 300,
   reentry_min_remaining_pct 0.3, max_reentries_per_window 0, queue_gate 0,
   gas 0. Scope: shadow universe only (xrp-15m, bnb-15m, eth-5m) +
   window range 2026-09-12T00:00Z → 09:11Z.
3. Scoped shadow baseline: same 55 in-coverage events from `trades.jsonl`.
4. Comparison (events, pairs, settles, realized P&L, expectancy) + bias verdict;
   >20% of shadow scoped realized P&L (|Δ| > $1.053 on $5.265) → file follow-up.
5. Post comparison table + verdict as an issue comment on #146.
6. Gate: `python -m pytest tests/test_backtest_engine.py -q` green.

### Out of scope
- Engine, dashboard, collector, or CLI code changes. `scripts/backtest.py`
  lacks --entry-delay/--entry-band flags and its `--help` crashes (argparse
  formatting bug) — known wart, do NOT fix here. `sweep_backtest.py` presets
  cannot express the winning config — do NOT extend here.
- Live money; re-running the shadow; the 9 pre-coverage events' fill replay
  (impossible without depth data).
- P&L accounting alignment is a comparison-step concern (engine reports in
  cents per window; shadow in USD per event — normalize before verdict).

### Known model deltas to disclose in the verdict (not to "fix")
- Paper sim fills on book touch; replay uses `fill_model="tape"` (conservative).
- Shadow ran `enable_leg_chase=true`; verify whether the engine models chase —
  if not, record as a one-sided divergence source.
- Re-entry forced off (0) to mirror the shadow; engine default would allow 1.

## 2. Task Breakdown

### Task 0: Branch + baseline [Setup]
- **Files**: (git state only)
- **Type**: Code
- **Skill**: `git-workflow-and-versioning`
- **Description**: Create feature branch `add/146-replay-cross-check` from
  updated main; record `python -m pytest tests/test_backtest_engine.py -q`
  baseline output.
- **Verify**: `git status` clean on new branch; baseline log saved.

### Task 1: Tick integrity gate [Research]
- **Files**: `run/ticks/ticks_2026-09-12.jsonl` (read-only)
- **Type**: Research
- **Skill**: `source-driven-development` (tool docs/flags as ground truth)
- **Description**: Run
  `python -m scripts.verify_tick_data run/ticks/ticks_2026-09-12.jsonl`;
  record error rate, gaps, late starts for the 00:00–09:11Z replay range.
  Abort-or-scope decision if the range is not intact.
- **Verify**: verifier exit code 0 + summary numbers captured in the
  comparison artifact.

### Task 2: Exact-config replay driver [Code/Backend]
- **Files**: `scripts/replay_shadow_check.py` (new),
  `runs/paper/2026-09-11_22-10_IDT/replay_comparison/` (new artifacts)
- **Type**: Code
- **Skill**: `test-driven-development` (assert config mirror + scope filter
  before accepting totals)
- **Description**: Small driver calling `backtest.replay()` with the §1
  BacktestParams mapping; restrict snaps to the 3-series universe and the
  00:00–09:11Z window range; write machine-readable replay totals JSON
  (events, pairs, settles, realized P&L, per-event expectancy) to the
  artifact dir. No changes to `backtest/`, `server/`, or `scripts/backtest.py`.
- **Verify**: driver prints params hash matching the intended config;
  replay totals JSON exists with n_events > 0; cents→USD normalization
  documented in-artifact.

### Task 3: Scoped shadow baseline [Code/Backend]
- **Files**: `runs/paper/2026-09-11_22-10_IDT/data/trades.jsonl` (read-only)
- **Type**: Code
- **Skill**: `test-driven-development`
- **Description**: Filter the 66 shadow events to the 55 in-coverage events
  (event epoch in [T0, T1], window start ≥ T0 − 1s); expected baseline:
  45 pairs + 10 settles, +$5.265 realized. Write `shadow_scoped.json`
  next to the replay totals.
- **Verify**: counts and P&L reproduce the pre-computed baseline exactly.

### Task 4: Comparison + bias verdict [Research]
- **Files**: `replay_comparison/` artifacts (new `comparison.md`)
- **Type**: Research
- **Skill**: `source-driven-development` (cite engine + shadow sources per number)
- **Description**: Line-by-line table replay-vs-shadow (events, pairs,
  settles, realized P&L, expectancy); verdict: validated / optimistic by X /
  pessimistic by X; apply the 20% rule (|Δ| > $1.053 → follow-up finding).
  Disclose the §1 model deltas (tape vs touch, chase, re-entry).
- **Verify**: every table cell traceable to either `trades.jsonl` or replay
  JSON; verdict sentence states magnitude + direction.

### Task 5: Publish + gate [Research]
- **Files**: (GitHub only + test run)
- **Type**: Research
- **Skill**: `verification-before-completion`
- **Description**: Post the comparison table + verdict as a `#146` issue
  comment (`gh issue comment 146 --body-file`); if divergence >20%, file the
  follow-up finding as a new issue and link it. Run
  `python -m pytest tests/test_backtest_engine.py -q` green.
- **Verify**: comment URL returned; test suite output green; follow-up filed
  or explicitly ruled out with the Δ number.
