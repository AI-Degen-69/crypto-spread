# Task Plan — Issue #273: Audit tick dataset readiness for trustworthy backtests

**Size tier:** Large — combines schema audit, statistical policy, streaming verification, API contract, and Tick Files UI.
**Task type:** Research + Code + API/Backend + Design/UI + QA.
**Issue:** #273

## Locked decisions
- Do not use one universal magic number for “enough data.” Readiness is claim-relative.
- Separate integrity health from research readiness.
- Count independent `(series, cid)` market windows separately from tick snapshots and `tape_delta` entries.
- Keep 5m and 15m coverage separate.
- Preserve replay math and existing `PASS/WARN/FAIL` behavior.

## Tasks

- [x] **TASK-1 [Research/Domain]**: Audit replay compatibility and define the readiness policy.
  - Target: `SPEC.md`, `CONSTRAINTS.md`, `backtest/engine.py`, `scripts/verify_tick_data.py`.
  - Result: required schema fields, claim-relative policy, explicit project thresholds, and statistical limitations documented.
  - Verify: `ticks_2026-09-18.jsonl` classified as `EXPLORATORY` with 7,180 snapshots, 30 windows, 6,666 tape entries, 10 market-duration pairs, one time block, and failed research checks.

- [x] **TASK-2 [Backend/Logic]**: Extend the streaming verifier with readiness metrics.
  - Target: `scripts/verify_tick_data.py`, `tests/test_verify_tick_data.py`.
  - Result: valid snapshots, unique `(series, cid)` windows, tape entries, market/duration coverage, time blocks, missing fields, rates, policy checks, and readiness classification.
  - Verify: complete, sparse, malformed, mixed-duration, duplicate, empty-tape, and gap behavior covered by tests.

- [x] **TASK-3 [Backend/API]**: Add a deterministic, machine-readable readiness contract.
  - Target: `server/osc_dash.py`, integration tests.
  - Result: readiness is exposed in verify reports, manifest aggregates, and per-file metadata; old sidecars without readiness are invalidated and rescanned.
  - Verify: cache, fingerprint, zero-window, manifest, and backwards-compatible endpoint tests.

- [x] **TASK-4 [Design/UI]**: Show readiness clearly on Tick Files.
  - Target: `server/osc_dash.py`, integration tests.
  - Result: aggregate and file rows expose Research Readiness; detail view separates readiness from integrity and shows failed measured-vs-required checks; metric labels identify Tick Snapshots, Market Windows, and Tape Entries.
  - Verify: served HTML vocabulary tests and browser verification.

- [x] **TASK-5 [QA/Regression]**: Validate policy against real files and backtest claims.
  - Target: verifier/API/UI tests and available `run/ticks` files.
  - Result: real-data report confirms September 18 is exploratory-only rather than research-ready.
  - Verify: focused pytest gate and real-data verifier output.

- [x] **TASK-6 [Hygiene]**: Simplify and document the readiness implementation.
  - Target: changed implementation/tests/docs.
  - Result: policy constants are centralized/versioned, no new dependencies were added, and live/replay math remains unchanged.
  - Verify: `git diff --check`, targeted tests, and compile checks.

## Evidence-based improvement proposal
Because the current verifier can report `WARN` for gaps/errors while the same file may still have enough observations for exploratory analysis, implement two independent dimensions—`integrity_status` and `research_readiness`—instead of collapsing them into one score. This is grounded in the existing report fields and avoids telling the operator that a technically imperfect file is either wholly unusable or research-grade.

## Out of scope
Collector protocol changes, replay/fill/exit math changes, obtaining more historical data, and live profitability claims.
