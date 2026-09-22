# CONSTRAINTS — Issue #297: pristine dataset — gate out windows with bounds-violation ticks

## Scope guard
- Files touched: `scripts/build_pristine_dataset.py`,
  `tests/test_build_pristine_dataset.py`, plus the three per-issue working files
  (`tasks/plan.md`, `tasks/todo.md`, this file + `SPEC.md`). Nothing else.
- Out of scope (hard): the sane-bounds values themselves (stay mid in [-0.01, 1.01],
  touch_pair in [0.50, 1.50] as in `verify_tick_data.py:205-213`); `verify_tick_data.py`
  behavior; the dashboard Sample Discrepancies panel; source tick files (read-only);
  any CLI flag for this gate (always-on, like the other six gates); a pristine re-build
  of `run/ticks/pristine/` (operator decision, post-merge — the dir is gitignored).
- The gate is named `bounds_violation` (singular) in `failing_gates`; the counter is
  `bounds_violations` (plural) in state/verdict/manifest — same naming split as
  `time_reversal` gate vs `time_reversals` counter.

## Zero regressions
- Targeted gate: `python -m pytest tests/test_build_pristine_dataset.py -q` passes —
  all existing tests plus new ones (issue asks for all 67 + new bounds tests).
- `python -m pytest tests/test_build_pristine_dataset.py -q -k bounds` passes (issue
  verification command).
- Full-repo sweep stays with CI on push (repo policy — never run locally).

## Anti-cheat
- No skipping, disabling, deleting, or weakening of any test or assertion.
- No new external dependency (`requirements.txt` untouched).
- No relaxation of existing gates to make fixtures pass.

## Determinism & data-safety invariants (from the module docstring — must survive)
- Sources never modified (existing `test_sources_untouched_hashes_before_after` stays green).
- Byte-identical re-run over unchanged inputs with the SAME code is preserved — the new
  counter is pure input-derived, no wall-clock, no set-iteration-order leaks into output.
- Pass-1 memory stays O(windows): one scalar counter only, no tick retention.
- Bounds-check semantics EXACTLY mirror `verify_tick_data.py:205-213`:
  None means skip; non-numeric or out-of-range means violation.
- A failing window never reaches pass-2 output: `passed_cids` derives from `passed`.
