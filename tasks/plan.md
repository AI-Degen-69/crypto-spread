# Plan — Issue #297: pristine dataset — gate out windows with bounds-violation ticks

Branch: `i297/pristine-bounds-violation-gate` | Issue: #297
Stack: Python 3, pytest · Size: **Small** (one module + its test file; no architectural
decision — the gate slots into an existing single-decision-path design) · Type: **Code**

## Resolved inputs (planning record)
- Issue supplies exact acceptance criteria, scope, file:line references, and one default
  assumption (same thresholds as `verify_tick_data.py:205-213`, no knob) — adopted.
- CodeRabbit posted a full implementation plan on the issue (comment 5780050825).
  Reviewed against the actual code: accurate on structure, file:line refs, and the
  critical first-tick seeding insight. Adopted as the task skeleton; this plan adds the
  layers it lacks (evidence scan, .get defensiveness, VERIFY_POLICY_NOTE honesty,
  full-file test gate, byte-identical guarantee wording).
- Sub-issue mapping skipped: 3 linear tasks stay tracked here + `tasks/todo.md`.

## Evidence gathered at planning time
- Real-data existence proof: `run/ticks/ticks_2026-09-18.jsonl` (7,180 ticks) has 1
  bounds-violating tick in 1 window — the bug exists in the live dataset.
- Existing pristine manifest: 6,129 windows scanned, 5,042 passed, 1,485,319 ticks
  written. Post-merge re-build will drop the violating windows — operator decision,
  recorded in CONSTRAINTS.md as out of scope for this branch.
- Dual-path architecture verified: `scan_windows` (streaming) and
  `evaluate_window_gates` (in-memory) both funnel through `judge_window`; the first
  tick seeds state in `_new_window_state` and never passes `_add_tick_to_state`.

## Spec
See `SPEC.md` — interface contracts frozen there (helper, seeding, judge, manifest,
policy note) plus the acceptance-criteria → test mapping table.

## Tasks
- **TASK-1** [Backend/Logic] · Size S · `scripts/build_pristine_dataset.py`
  Add `_tick_out_of_bounds(tick)` helper (thresholds hard-coded, None-safe, non-numeric
  = violation). Seed `bounds_violations` in `_new_window_state` from the first tick
  (error_ticks pattern). Increment in `_add_tick_to_state`. Add the `bounds_violation`
  gate to `judge_window` via `agg.get("bounds_violations", 0)`; sync the `no_ticks`
  early-return dict; add `bounds_violations` to the normal verdict dict. Add the key to
  `manifest_keys`. Extend `VERIFY_POLICY_NOTE` with the bounds-gate clause.
  · Depends on: — · Verify: module imports; new unit tests (TASK-2).

- **TASK-2** [Tests] · Size S · `tests/test_build_pristine_dataset.py`
  New `TestBoundsViolationGate` class near the existing gate tests (lines 91-166):
  (a) touch_pair=1.74 mid-window → passed False, gate in failing_gates, counter == 1;
  (b) mid=1.02 and mid=-0.02 → gate fires; (c) violation at tick index 0 → counted
  (seeding path); (d) None mid/touch_pair → no violation (None-safe); (e) non-numeric
  value → violation; (f) clean pristine_ticks() → no bounds_violation gate;
  (g) e2e modeled on test_failing_window_absent_from_output: fixture with one clean +
  one violating window (cid 0xbounds) → violating cid absent from output files and
  passed_cids; manifest record has passed False + bounds_violation in failing_gates.
  · Depends on: TASK-1 · Verify: `python -m pytest tests/test_build_pristine_dataset.py
  -q -k bounds` (issue verification command).

- **TASK-3** [Closeout] · Size XS · closeout
  Full targeted gate `python -m pytest tests/test_build_pristine_dataset.py -q` (all
  existing + new); confirm byte-identical + sources-untouched tests still green; tick
  todos; `git diff --stat` shows only the two in-scope files + working files.
  · Depends on: TASK-1, TASK-2 · Verify: targeted pytest green.

Checkpoints: after TASK-2 (gate proven end-to-end), after TASK-3 (clean closeout).

## Improvement proposal (recorded)
- **Adopted (robustness):** defensive `agg.get("bounds_violations", 0)` in
  `judge_window` instead of direct indexing — evidence: `evaluate_window_gates:95`
  builds a minimal dict without the key for the empty-window path; today it is saved by
  the `no_ticks` early return, but `.get` costs nothing and protects future callers.
