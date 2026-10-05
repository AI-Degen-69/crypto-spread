Branch: i451/fix-paper-dead-zone-expiry-exit-books-the-same-leg | Issue: #451

# Implementation Plan — Dead-Zone Expiry Exit Fires Once Per Leg

## Size & Stack
- Tier: **Small** — one predicate in one source file (`strategy/live_trader.py:4907`) plus two regression tests; single decision (mirror the adjacent `exit_taken` guard, no new abstraction).
- Task type: **Debug + Code**. Stack: Python, pytest (`tests/test_live_trader.py` + neighbors `tests/test_dead_zone_parity.py`, `tests/test_stop_orders.py`). No UI, no API, no dependency change.

## CodeRabbit Intake Note
- Adopted: 2-phase skeleton (one-predicate guard + two repeated-tick regression tests near `test_naked_leg_dead_zone_force_exits_when_close`); seam pointers (4907 predicate, 4947–4949/4973–4975 guard pattern, 1810 finalize, `_reset_round_to_clean` reset); test setup pattern (`_naked_market` + `_open_50_50_quotes`, dead-zone timing `now + 261.0s`).
- Rejected: nothing structural — the plan was already minimal (2 tasks); verification folded into a third XS task per this station's convention.
- `[UNVERIFIED]` at intake: none left — every cited seam spot-checked against live code (4907 missing guard confirmed; stop-trigger guards confirmed; `exit_taken = True` at 1810 confirmed; `_resolve_exit_bid(self, mstate, side)` tuple signature confirmed vs the 3-arg fallback call at 4925; `exit_taken = False` reset at 5033 confirmed).

## Resolved Open Questions (from code, not asked)
- **No `needs-answers` label, no Open questions section** — the issue body names the exact line, the pattern to mirror, and the acceptance gate. Nothing to ask.
- **Pending-exit safety:** the reconcile block (4891–4903) returns early once `exit_taken` is set, and pending exits keep `exit_taken == False` until `_execute_stop_exit` finalizes — so the new guard cannot starve reconciliation.
- **New-round re-entry:** `_reset_round_to_clean` clears `exit_taken` (line 5033) together with the fill flags, so a fresh naked leg in a later round can still expiry-exit. The guard suppresses only repeats within the current round.
- **Malformed fallback at 4925:** `self._resolve_exit_bid(slug, mstate, naked_side)` passes 3 args to a `(self, mstate, side)` tuple-returning helper — confirmed latent defect, out of scope here, to be reported as a separate issue per the ticket's scope line.
- **type-design-analyzer / code-explorer:** skipped — no interface change, execution path traced directly in `live_trader.py`.

## Spec (embedded — Small tier)
- Goal: one dead-zone window with an unpaired leg books exactly one expiry-exit trade, no matter how many ticks remain in the dead zone.
- Acceptance: (1) repeated dead-zone ticks after the first expiry exit add no trades and change no PnL/counters; (2) the legitimate first exit fires and records exactly as before; (3) `python -m pytest tests/test_live_trader.py -q` green with no regressions.
- Out of scope: stop trigger thresholds, trigger-note wording (#452), live venue cancel flow, backtest engine, dashboard rendering, the 4925 fallback call.

## Improvement Proposal (adopted — edge-case hardening)
- Evidence (code): "`mstate.exit_taken = False`" at line 5033 inside `_reset_round_to_clean`, whose docstring says "all per-round order, fill, chase, and drift fields reset to clean so standing conditions can evaluate a fresh entry. Cumulative metrics (pairs_count, stops_count, realized_pnl_usd, trades) are preserved."
- Proposal: cover the guard's far boundary in Task 2 — after a manual `_reset_round_to_clean`, a fresh naked leg in the dead zone must still expiry-exit exactly once. This proves the guard suppresses repeats without over-suppressing new rounds. Adopted into Task 2 (not a scope expansion: it asserts the acceptance criterion survives the reset path).

## Tasks

### [x] Task 1: [Debug] Add the `exit_taken` guard to the expiry predicate in `strategy/live_trader.py` (S)
- **Files:** `strategy/live_trader.py` (dead-zone expiry predicate, line 4907)
- **Depends on:** none (riskiest decision — the exact guard placement — goes first)
- **Description:** add `not mstate.exit_taken` to the condition at line 4907, matching the form and position of the UP/DOWN stop guards at 4947–4949 and 4973–4975. Keep fill inequality, `status != "STOP_EXIT_PENDING"`, and `in_dead_zone`. Leave the block body (status assignment, staged-stop clearing, bid selection, `_execute_stop_exit` call), the 4925 fallback, and all other triggers untouched.
- **Skill:** `debugging-and-error-recovery`
- **Verification:** Task 2 repeated-tick test fails before, passes after; first-exit values byte-identical to pre-fix behavior.

### [x] Task 2: [Debug] Repeated-tick regression tests in `tests/test_live_trader.py` (S)
- **Files:** `tests/test_live_trader.py` (near `test_naked_leg_dead_zone_force_exits_when_close`, line 3015)
- **Depends on:** Task 1
- **Description:** (a) repeated-tick test: reuse `_naked_market` + `_open_50_50_quotes`, fill UP only, first dead-zone tick books one `STOP_EXIT_UP` trade — then send 2+ more dead-zone ticks with a changed bid (still above the stop threshold) and assert trade count, price, shares, PnL, `realized_pnl_usd`, `stops_count`, `trades_count`, and `STOP_EXIT` status all unchanged; (b) stop-before-dead-zone test: ordinary stop fires first, then dead-zone ticks add nothing; (c) reset-boundary assertion: after `_reset_round_to_clean`, a fresh naked leg in the dead zone expiry-exits exactly once.
- **Skill:** `test-driven-development`
- **Verification:** new tests fail on the pre-fix predicate, pass after Task 1.

### [x] Task 3: [Backend/Logic] Targeted verification sweep (XS)
- **Files:** none (verification only)
- **Depends on:** Task 2
- **Description:** run `python -m pytest tests/test_live_trader.py -q`, `tests/test_dead_zone_parity.py -q`, `tests/test_stop_orders.py -q`. No full-suite local run (CI owns it). Confirm glossary terms in comments ("trading engine", "paper mode").
- **Skill:** `incremental-implementation`
- **Verification:** all three suites green; branch clean except the two intended files.

## Checkpoints
- After Task 1: predicate carries the guard; nothing else in the engine changed.
- After Task 2: duplicate booking is provably dead (fail-before/pass-after), first-exit values preserved, reset boundary covered — ready for `iii-build-plan` handoff review.
