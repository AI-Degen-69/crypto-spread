# Task Plan — Issue #280: Charter the golden tick dataset for backtesting

**Issue:** #280 — Charter the golden tick dataset for backtesting (`ready-for-agent`)
**Size tier:** Standard — 3 authored/updated docs + 2 name-registry files; one architectural
decision (golden set = verified day files + manifest, not a concatenated blob). Zero code changes.
**Task type:** Docs (+ Research grounding). Reviewer axes at Station IV: language specialist +
test engineer (no code to security-review).
**Stack:** Markdown docs; pytest targeted runner (`tests/test_verify_tick_data.py`,
`tests/test_collect_ticks_smoke.py`).

## Issue-derived interface contract

- `docs/golden-tick-dataset.md` — new charter doc. Sections: Definition, Collection plan,
  Certification plan, Replay-speed budget, Re-certification policy. Every number maps to an
  existing verify metric (see SPEC.md Deliverable 1–4).
- `docs/glossary.md` — one new entry in "Data": **the golden dataset**. No renaming of anything
  else; glossary wins over older comments (AGENTS.md rule).
- `AGENTS.md` — one line in the `docs/` structure list; no command changes (the golden set is
  consumed by existing CLI entrypoints, not a new one).
- No function signatures, schemas, endpoints, or thresholds change.

## Dependency order and tasks

### Phase 1 — the charter document

- [x] **TASK-1 [Docs] Write `docs/golden-tick-dataset.md` — definition + quality bar**
  - **Files:** `docs/golden-tick-dataset.md` (new).
  - **Build:** per SPEC Deliverable 1 — the agreed name ("the golden dataset"), location
    `run/ticks/golden/`, structure = verified per-day files + `golden_manifest.json` (not one
    concatenated file, grounded in `backtest/index.py:117-124` directory support and #281's
    either/or), and the concrete quality bar: per-day `PASS` + `COMPLETE CAPTURE`; set-level
    `RESEARCH_READY` with headroom (≥5 days, ≥500 windows, ≥50 windows/market, all 10 series,
    gap rate ≤0.05, zero corrupt/collector errors); continuity budgets (late start ≤5s,
    early cutoff ≤5s, zero time reversals).
  - **Helper:** `spec-driven-development`.
  - **Verify:** self-review against CONSTRAINTS.md "no invented metrics" — every number traceable
    to `scripts/verify_tick_data.py` output fields.

- [x] **TASK-2 [Docs] Collection + freeze rules in the charter**
  - **Files:** `docs/golden-tick-dataset.md`.
  - **Build:** per SPEC Deliverable 2 — watchdog-guarded `collect_ticks` for 5 consecutive full
    UTC days, measured throughput (~1.5–1.7GB/day, ~160k snaps), day-boundary rotation handling
    (`now_day_key`, `collect_ticks.py:369`), per-day verify gate, quarantine-with-reason policy,
    and the freeze rule: no collector-code changes during a golden capture (#174 must wait).
  - **Helper:** `planning-and-task-breakdown`.
  - **Verify:** the plan is executable as-is with current tooling — every command cited exists
    (`collect_ticks`, `collector_watchdog`, `verify_tick_data`) with its real flags
    (`docs/operations.md` runbook cross-checked).

- [x] **TASK-3 [Docs] Certification sequence + re-certification policy in the charter**
  - **Files:** `docs/golden-tick-dataset.md`.
  - **Build:** per SPEC Deliverable 3 — the exact command sequence (per-day verify → set-level
    verify → fresh `.idx` per day via `backtest.index.build_index`/`is_fresh` → replay speed
    check), plus promotion/re-certification policy keyed to `READINESS_POLICY_VERSION`
    (`scripts/verify_tick_data.py:32`).
  - **Verify:** walk the sequence by hand against #281's acceptance criteria — each #281 checkbox
    must map to one step here.

- [x] **TASK-4 [Docs] Replay-speed budget grounded in measurements**
  - **Files:** `docs/golden-tick-dataset.md`.
  - **Build:** per SPEC Deliverable 4 — budget statement (≤~1s per window warm-sidecar) with the
    two measured baselines cited: 467MB → 34.07s no-index scan
    (`docs/measurements/issue-221-gil-contention.json`) and ~50ms indexed cid jump
    (`backtest/index.py:1-9`); requirement for fresh `.idx` sidecars at certification time.
  - **Verify:** numbers in the doc match the measurement JSON and `index.py` docstring exactly.

### Phase 2 — name registry wiring (depends on TASK-1 wording)

- [x] **TASK-5 [Docs] Register the agreed name**
  - **Files:** `docs/glossary.md`, `AGENTS.md`.
  - **Build:** glossary "Data" table gains **the golden dataset** (what it is + link to the
    charter); "golden day" and "golden manifest" as sub-terms. `AGENTS.md` docs listing gains one
    line: `golden-tick-dataset.md` (the canonical backtest dataset charter).
  - **Helper:** glossary-first naming convention (`docs/glossary.md` wins over stale comments).
  - **Verify:** grep the repo for the term — no conflicting older name introduced; `AGENTS.md`
    note-in-particular style respected ("golden" names the dataset, never an execution mode).

### Phase 2b — regression gate

- [x] **TASK-6 [QA] Targeted test gate**
  - **Files:** none (run-only).
  - **Build:** run `python -m pytest tests/test_verify_tick_data.py tests/test_collect_ticks_smoke.py -q`.
  - **Verify:** exit 0; no test modified.

## Acceptance criteria → task map (from the issue)
1. Charter exists with concrete, verifiable numbers mapped to existing verify metrics → TASK-1.
2. Charter contains collection plan + certification command sequence, achievable with current
   collector throughput → TASK-2, TASK-3.
3. Charter states a replay-speed budget and how the `.idx` sidecar meets it → TASK-4.
4. `AGENTS.md` + `docs/glossary.md` name the golden dataset with one agreed term → TASK-5.
5. Targeted pytest gate passes → TASK-6.

## Explicit out of scope
- Running the capture, assembling the golden set, building sidecars (#281).
- Any code, threshold, collector, or backtest change.
- #279's UI scope.
