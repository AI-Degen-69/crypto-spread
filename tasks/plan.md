# Task Plan — Issue #148: Post-pilot: unify execution entrypoints

**Size tier:** Standard — auditing and documenting entrypoints across `AGENTS.md`, deprecating legacy `bot/paper_bot.py`, deleting dead `ten-bankrolls/`, and adding targeted regression tests in `tests/test_entrypoints.py`.
**Task type:** Docs / Code (entrypoint auditing, deprecation instrumentation, architecture documentation, test coverage).

## Context & Problem
- Prior to the live micro-pilot, multiple overlapping execution scripts were introduced: `bot/paper_bot.py` (legacy standalone polling script with non-functional `--live` stub), `ten-bankrolls/` (isolated bankroll experiments), alongside `strategy/live_trader.py` (the canonical production live/paper engine).
- Now that the pilot has concluded and `strategy/live_trader.py` is the established single source of truth for trading execution, the repository needs unambiguous entrypoint ownership documented in `AGENTS.md`, dead entrypoints (`ten-bankrolls/` and `bot/`) completely deleted, and verification tests in place.

## Proposed Improvement (Adopted by default)
- Under the completely-dead code exception and operator instruction ("אז אם הוא מיושן למה לא לנקות את התיקיות משם"), both unmaintained legacy directories (`ten-bankrolls/` and `bot/`) are pruned cleanly, leaving zero dead code or non-functional stubs.

## Tasks

- [x] **TASK-1 [Docs/Architecture]**: Document execution entrypoints and ownership in `AGENTS.md`
  - Target: `AGENTS.md`
  - What is built:
    - Add dedicated section `## Execution Entrypoints & Ownership` in `AGENTS.md`.
    - Provide a single clear table covering each entrypoint: File, Role, Status (Canonical vs Removed), and Owner.
    - Reconcile `strategy/live_trader.py` (sole canonical engine for live/paper) vs `scripts/shadow_ev_pilot.py` (paper EV runner) vs `server/osc_dash.py` (cockpit) vs `bot/paper_bot.py` (removed) vs `ten-bankrolls/` (removed).
  - Helper skill: `documentation-and-adrs`
  - Verify: Section present and accurately cross-referenced.

- [x] **TASK-2 [Code/Pruning]**: Remove legacy `bot/` directory under completely-dead exception
  - Target: `bot/paper_bot.py`
  - What is built:
    - Entire `bot/` directory deleted from filesystem and git index following operator directive.
    - Remove `bot/` references from `.gitignore` and documentation.
    - Verified absent via `test_bot_directory_deleted()`.
  - Helper skill: `code-simplification`
  - Verify: Directory removed, git status clean of untracked files in that path.

- [x] **TASK-3 [Cleanup/Pruning]**: Remove dead `ten-bankrolls/` directory from repository
  - Target: `ten-bankrolls/`
  - What is built:
    - Delete `ten-bankrolls/` directory and its contents (`run_one.py`, `watcher.py`, `100/`..`1000/`, `README.md`).
    - Remove references to `ten-bankrolls/` from `AGENTS.md`.
  - Helper skill: `code-simplification`
  - Verify: Directory removed, git status clean of untracked files in that path.

- [x] **TASK-4 [QA/TDD]**: Add targeted tests in `tests/test_entrypoints.py`
  - Target: `tests/test_entrypoints.py`
  - What is built:
    - Test that `strategy.live_trader.LiveTraderEngine` is importable as the canonical engine.
    - Test that `AGENTS.md` explicitly documents entrypoint statuses.
    - Test that `ten-bankrolls/` and `bot/` directories are absent from the filesystem.
    - Verify `tests/test_docstrings.py` achieves 100% coverage across all files.
  - Helper skill: `python-testing`
  - Verify: `python -m pytest tests/test_entrypoints.py tests/test_docstrings.py -q`.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | Inspection of `AGENTS.md` |
| TASK-2 | Path absence check for `bot/` (`test_bot_directory_deleted`) |
| TASK-3 | Path absence check for `ten-bankrolls/` (`test_ten_bankrolls_deleted`) |
| TASK-4 | `python -m pytest tests/test_entrypoints.py tests/test_docstrings.py tests/test_live_trader.py -q` |

## Post-build gates (Station IV)
- `python -m pytest tests/test_entrypoints.py tests/test_docstrings.py -q` passes in <2s.
- `python -m pytest tests/test_live_trader.py -q` passes without regression.
