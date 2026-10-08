# Plan — Issue #467: scope the replay exit-threshold mirror to the run's own universe

`Branch: i467/fix-scope-the-replay-cross-checks-exit-threshold-m | Issue: #467`

## Stack (auto-detected)
- Python 3.12, pytest; single-file fix in `scripts/replay_shadow_check.py`, tests in `tests/test_replay_shadow_check.py`.
- Seams verified verbatim: `assert_config_mirror` def `:216`, `for slug in UNIVERSE:` `:246`, driver call `:419` inside the four-leg loop, `UNIVERSE` triple `:41`, `scope.universe` in hand at `:408-419`.

## Size tier + rationale
- **Small** — one function signature + loop, one call site, tests. Straightforward once read.
- **Task type:** Code.

## Step 0C — quick-fix divert
- Labels re-read live: `bug`, `ready-for-agent` — no `quick-fix` label → continue, no gate text.

## Step 0A — CodeRabbit plan + open questions
- CodeRabbit plan present (issue comments). Adopted: optional `universe` param defaulting to `UNIVERSE`; driver passes `scope.universe`; tests with a foreign slug (pass / missing / wrong-value) + driver-passes-list test; `build_params` and 0.05 untouched.
- Rejected: "mark N1 as fixed" in `docs/issues/465-noticed-but-not-touching.md` — the ledger's allowed statuses are `open/published/duplicate/dismissed` (no `fixed`), and N1 already records resolution `#467`. Touching it would corrupt the ledger contract for zero information.
- Open question ("how the scope reaches the assertion") resolved from code: the driver holds `scope` at the call site, and a defaulted 5th param keeps every existing 3/4-arg call working — the default assumption holds, no operator question asked.
- `code-explorer` skipped (Small, single file, all seams read directly).

## Step 1 — domain routing
- `incremental-implementation` (atomic slices). No UI/backend split; single `[Code/Logic]` tag, verification = pytest on the touched module.

## Step 2 — spec (embedded; Small → no SPEC.md)
- Goal: per-slug exit-threshold checks iterate `scope.universe`; bare #146 path unchanged; values stay 0.05.
- Acceptance (issue verbatim): scope's universe iterated, not the module constant; a test with a universe differing from `UNIVERSE` fails when a slug lacks a threshold; bare path unchanged; `tests/test_replay_shadow_check.py` green.
- Out of scope: default path behavior, threshold values, other knobs.

## Step 4 — interface contract
- `assert_config_mirror(params, recorded, gates_on, pair_cap=0.98, universe=None)`; `None` → module `UNIVERSE` via explicit `is None`. Positional-or-keyword (existing calls untouched). Driver calls with `universe=scope.universe`.

## Step 5 — improvement proposal
- None beyond the issue + adopted CodeRabbit skeleton: the only evidence-grounded edge (`is None` vs truthiness for `()`) is already inside T1. No filler proposal.

## Step 6 — dependency graph + tasks
- Graph: T2 depends on T1 (needs the param); T3 depends on T1. Order: T1 → T2 → T3.

### T1 [Code/Logic] (S) — optional universe param on the check [x]
- Files: `scripts/replay_shadow_check.py` (`:216-247` only).
- Build: add last param `universe: tuple[str, ...] | None = None`; `if universe is None: universe = UNIVERSE`; loop over local `universe`. Keep error text, 0.05, arg order.
- Depends on: —. Verify: existing tests pass unchanged (default path identical).

### T2 [Code/Logic] (XS) — driver passes the run's universe [x] (built + committed together with T1: one 7-line commit 2ddf0b0)
- Files: `scripts/replay_shadow_check.py` (`:419` only).
- Build: `assert_config_mirror(params, recorded, gates_on, pair_cap, universe=scope.universe)` in the four-leg loop.
- Depends on: T1. Verify: `grep universe= scripts/replay_shadow_check.py`; bare path still defaults.

### T3 [Code/Logic] (S) — tests for a foreign universe [x] (28 passed: 24 existing + 4 new)
- Files: `tests/test_replay_shadow_check.py` (append only).
- Build with `dataclasses.replace` (already imported): params carrying e.g. `sol-up-or-down-5m: 0.05` pass with `universe=("sol-up-or-down-5m",)`; same universe with the slug missing raises `AssertionError` matching `exit mirror gap`; wrong value raises; default call (no universe) still checks `UNIVERSE`.
- Depends on: T1. Verify: `python -m pytest tests/test_replay_shadow_check.py -q` green.
