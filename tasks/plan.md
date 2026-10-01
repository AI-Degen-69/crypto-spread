# Plan — Issue #373

`fix(market-data): add missing GAMMA_HOST/CLOB_HOST constants to strategy.markets`

Branch: `i373/add-missing-gamma-clob-host-constants-to-markets`
Base: `master` @ `0a4421f`. Claimed: assignee `AI-Degen-69`.

## Classification (ECC right-sizing)
- **Size tier: Small.** One production file gains two module-level string
  constants; one test file added. No behaviour change, no interface redesign.
- **Task type: Code** (primary), with a **Debug** character — the defect is a
  long-standing `ImportError` discovered by reading call sites.
- **Environment:** Python, `pytest`. Targeted tests only; CI gates full suite.

## Evidence gathered
| Fact | How verified |
|---|---|
| `strategy.markets` has no `CLOB_HOST` | `python -c "from strategy.markets import CLOB_HOST"` → ImportError, exit 1 |
| Broken consumer | `scripts/record_raw_socket_session.py:29` imports it |
| Canonical values | `scripts/collect_ticks.py:89-90` |
| Second copy exists | `strategy/live_trader.py:31-32` (not rewired here → #374) |
| Existing precedent in the module | `TRADES_API` at `strategy/markets.py:232` |
| No test covers the recorder | grep for `record_raw_socket_session` across `tests/` → 0 hits |
| Collector regression baseline | `pytest tests/test_collect_ticks_smoke.py -q` → 39 passed |

## Dependency graph
```
T1 (constants in strategy/markets.py)
 └─> T2 (import smoke test)      [needs T1 to assert the values exist]
      └─> T3 (regression run)    [needs T1+T2 green]
```
Linear, single-file-risk-first ordering. No parallelisable independent work —
T2 is meaningless before T1 exists.

## Tasks

### T1 — Define the venue host constants
- **Status:** [x] done — `strategy/markets.py:31-32` (verified: import exits 0,
  negative check proved the new test fails without it)
- **Size:** XS (two lines + comment)
- **Domain:** Code / market data
- **Files:** `strategy/markets.py` (insert near the `MARKET_TIMEOUT` /
  `EVENTS_TIMEOUT` block, ~line 22-23, above `_POOL_SIZE`)
- **Helper:** — (direct edit)
- **Depends on:** —
- **Do:** add `GAMMA_HOST` and `CLOB_HOST` with byte-identical values to
  `scripts/collect_ticks.py:89-90`. Comment states why they live here now:
  the book-layer single source of truth must expose the host its `full_book`
  call sites pass. Must not rewire any consumer (that is #374/#375).
- **Verify:** `python -c "from strategy.markets import CLOB_HOST, GAMMA_HOST, full_book"`

### T2 — Smoke test that would have caught the ImportError
- **Status:** [x] done — `tests/test_markets_hosts.py`, 35 tests passing;
  **checkpoint proven**: reverting T1 makes 4 of them fail (stash/unstash check)
- **Size:** S (one new file)
- **Domain:** Code / test
- **Files:** new `tests/test_markets_hosts.py`
- **Helper:** — (or `test-driven-development`)
- **Depends on:** T1
- **Do:** three tests, no network:
  1. `import scripts.record_raw_socket_session` succeeds — the exact regression
     that was broken. Import-only, no side effects on disk.
  2. `strategy.markets.GAMMA_HOST` / `CLOB_HOST` equal the canonical literals.
  3. `strategy.markets.CLOB_HOST` is the value `collect_ticks` uses, asserted
     against the module attribute (not a retyped literal) so #374's rewiring
     cannot silently drift.
  Plus the adopted improvement: a parametrised import test over every
  repo-local `scripts/*.py` module (4th test, 32 parametrisations).
- **Verify:** `python -m pytest tests/test_markets_hosts.py -q` → all pass
  (confirm the test fails when T1 is reverted — a test that cannot fail is not
  a regression guard). **Confirmed: 4 failed / 31 passed with T1 removed.**

### T3 — Targeted regression sweep
- **Status:** [x] done — all four verify commands green (see results below)
- **Size:** XS
- **Domain:** Code / verification
- **Files:** none (verification only)
- **Helper:** `verification-before-completion`
- **Depends on:** T1, T2
- **Do:** run the acceptance-criteria commands, record output in the PR.
  Deliberately NOT the full suite (`AGENTS.md`: ~932 tests / ~96s, CI is the
  merge gate).
- **Verify:**
  - `python -c "from strategy.markets import CLOB_HOST, full_book"` → exit 0
  - `python -m pytest tests/test_collect_ticks_smoke.py -q` → 39 passed
  - `python -m pytest tests/test_markets_hosts.py -q` → passed
  - `git diff --stat` shows only `strategy/markets.py` + the new test file

**Checkpoint after T2** — constants defined and the regression guard is proven
able to fail.

## Improvement proposal (adopted by default)
Extend the new smoke test to import every module under `scripts/` so a future
missing-constant `ImportError` is caught by CI instead of by a human reading
call sites — one line of parametrisation over `pkgutil.iter_modules`, keeping the
guarantee the issue already asks for. Drops only on explicit operator rejection.

**Adopted, with one implementation change recorded:** `scripts` is a *namespace*
package here (no `__init__.py`) and Python merges it with the `site-packages`
copies (pywin32 ships `scripts/profile-tui.py`, which fails on Windows with
`No module named 'termios'`). `pkgutil.iter_modules` therefore yields foreign
modules CI does not control. The test globs `(ROOT / "scripts").glob("*.py")`
instead — repo-local only, deterministic. Measured: 32 modules, all import, 1.9s,
zero file writes and no network at import time.

## Verification results (T3)
| Check | Result |
|---|---|
| `python -c "from strategy.markets import CLOB_HOST, full_book"` | exit 0 |
| `python -c "import scripts.record_raw_socket_session"` | exit 0 |
| `pytest tests/test_collect_ticks_smoke.py -q` | 39 passed (unchanged baseline) |
| `pytest tests/test_markets_hosts.py -q` | 35 passed |
| combined `test_markets_hosts` + `test_collect_ticks_smoke` + `test_entrypoints` | 78 passed |
| negative check: T1 reverted → `test_markets_hosts.py` | 4 failed, 31 passed (guard is real) |
| `git diff --stat` | only `strategy/markets.py` (+9) + new test file |

Full suite not run locally — CI is the merge gate (`AGENTS.md` policy).

## NOTICED-BUT-NOT-TOUCHING (future-issue candidates, not touched here)
- Untracked `SPEC.md` at repo root (Issue #353's spec, deleted in HEAD by
  `0a4421f` but present on disk as an orphan working copy) — out of scope.
- `scripts/measure_5m_oscillation.py:37-38` still carries its own legacy host
  pair — deliberately left for #374.

## Rejections recorded
- **Rewire `collect_ticks.py` / `live_trader.py` to the new constants** —
  rejected here; explicitly Issue #374's scope. Recorded so it does not resurface.
- **Delete the legacy host constants in `scripts/measure_5m_oscillation.py`** —
  rejected here; not in scope, not requested, would touch an unrelated collector.
- **Rename `CLOB_HOST` to something like `CLOB_API_BASE`** — rejected; #374
  will consume the name `CLOB_HOST`, renaming now buys nothing.
- **Also fix `collect_ticks.py:280` clock divergence** — rejected; the issue
  itself defers it pending an owner decision.

## Handoff
Station III — `/iii-build-plan auto`