Branch: i459/fix-dead-zone-expiry-exit-bid-args | Issue: #459

# Implementation Plan — Dead-Zone Expiry Bid Resolution

## Size & Stack
- **Tier: Small** — the production change is one branch of one private method (`_update_market_strategy`); the second touched file is its test. No interface change, no schema change, no new dependency.
- **Task type: Debug** (+ Core Code). Stack: Python 3.12, pytest, targeted suites only (`tests/test_live_trader.py` and the dead-zone parity file). No UI, no API, no CI gate on pytest (the workflow was removed) — the merge gate is CodeRabbit + the local targeted runs.

## Defect
`strategy/live_trader.py:4934` calls `self._resolve_exit_bid(slug, mstate, naked_side)` — three positional args — while the method is `def _resolve_exit_bid(self, mstate: MarketLiveState, side: str) -> Tuple[float, str]` at `:5087`. When the naked leg has no direct book bid, the call raises `TypeError`; `_run_loop` logs it once per second and retries, so the configured exit never fires and **every market after the offending one in dict order loses its strategy update each tick**. Even with the right arity, `:4936` (`naked_bid > 0.0`) would compare the returned tuple to a float.

## CodeRabbit Intake Note
- **Adopted:** the two-part skeleton (correct arity + unpack; hold on `RuntimeError`), the *return* rather than fall-through choice, the log level/message style of the `:1668` precedent, the monkeypatch technique for a deterministic failure test (a two-argument fake also fails pre-fix, so the test is a true repro), and its do-not-touch list.
- **Rejected:** its per-phase ceremony (two phases re-labelled as one task + one test task); and its Design Choice 4 ("leave the ledger unchanged") is adopted for this PR — the `#451` N1 row already reads `published | #459`, and the merge reference is appended by Station VI at closeout, which owns the ledger.
- **`[UNVERIFIED]` left:** none. Every seam it cited was spot-checked against live code (`:4934` call, `:5087` signature, `:1668` precedent, `_run_loop` catch at `:3867-3869`, market loop at `:3872-3886`).

## Resolved Open Questions (from code, not asked)
- **What should happen when every ladder stage fails?** The helper raises `RuntimeError` (`:5139`). The repo already answered this for the sibling caller: `_execute_stop_exit` at `:1658-1674` logs `"[%s] Stop exit for %s leg held: no executable bid in book or latch"` at warning level and returns without inventing a price (Invariant 0, #224). The dead-zone branch adopts the same contract and returns from this market's update, so the leg stays eligible (`exit_taken` False, status not `STOP_EXIT_PENDING`) and is re-evaluated on the next tick. `needs-answers` label removed on this basis.
- **Is the opposite-buy cancellation safe to repeat on the retry tick?** Verified in code: it acts only when `order_status_*` is `RESTING` and sets it to `CANCELLED`, so a repeated tick finds a non-`RESTING` status and does nothing.
- **Can a unit test reach the `None` branch?** Yes — the poll's `up_book:{...}`/`down_book:{...}` payload assigns `mstate.up_bid = ubook.get("best_bid")` verbatim (`:4136`, `:4155`), so passing `best_bid: None` for the naked side makes the direct bid `None` while a real opposite ask still lets ladder stage 2 (binary complement) resolve a price.

## Spec (Small tier — embedded, no `SPEC.md`)
- The dead-zone expiry branch resolves the naked bid through the shared ladder with the correct arity, uses the returned price (never the tuple), and holds the exit when the ladder cannot produce an executable mark.
- Out of scope: the ladder, `_execute_stop_exit`, `_tick_all_markets`/`_run_loop` error handling, the backtest parity path, every config knob, and any new fallback constant.
- Acceptance: the configured exit still fires exactly once when the book can be marked (existing #451 contract), and when it cannot, nothing exits, no price is invented, and the engine keeps serving the remaining markets.

## Improvement Proposal
**Not adopted — recorded with reason (scope expansion, opt-in only).** Evidence, verbatim from `strategy/live_trader.py:3872-3886`: `markets_snapshot` is iterated and `res = ...` results are dispatched with `if isinstance(res, Exception): log.warning(...)` **before** `if res: self._update_market_strategy(slug, res, now)` — the strategy call itself has no guard, and `_run_loop` (`:3867-3869`) only catches at whole-tick level. So any single market's exception starves every later market in that tick, which is exactly the blast radius this defect produced. Proposal: isolate each market's update in its own `try/except Exception` inside that loop, logging the slug and continuing. It is a behavior change the issue never asked for, so it stays **out of this plan**; it is reported to the operator as an opt-in (a follow-up issue is the natural home once #459 lands).

## Tasks

### [x] Task 1: [Debug] Write the failing regression tests (S)
- **Files:** `tests/test_live_trader.py`
- **Depends on:** none (risk-first: prove the repro before touching production code)
- **Description:** three tests modelled on the existing `test_naked_leg_dead_zone_force_exits_when_close` fixture pattern (`_naked_market(now, elapsed=10.0, duration=300.0)` + `_open_50_50_quotes` + `_update_market_strategy` at `now + 261.0`, i.e. 29s remaining inside the 10% dead zone):
  (a) **success through the real ladder** — poll with `up_book: {"best_bid": None, ...}` on the naked UP side and a real `down_book` ask, then assert one exit fires with the ladder's complement price (`1.0 - down_ask`, rounded) and that no `TypeError` escapes;
  (b) **hold on `RuntimeError`** — `monkeypatch.setattr(engine, "_resolve_exit_bid", fake)` with a two-argument fake that raises `RuntimeError`; assert the tick returns cleanly with `exit_taken is False`, `status != "STOP_EXIT_PENDING"`, and zero trades;
  (c) **hold then exit once** — the fake raises on the first call and delegates to the real method afterwards; assert the retry tick books exactly one exit (the #451 one-exit contract survives the hold).
- **Skill:** `test-driven-development`
- **Verification:** the three tests must **fail before** Task 2 — (a) and (c) with `TypeError: ... takes 3 positional arguments but 4 were given`, (b) with the same `TypeError` propagating out of `_update_market_strategy`. Record the verbatim failure in the Task 2 commit message.

### [x] Task 2: [Backend/Logic] Fix the call site and hold on `RuntimeError` (XS)
- **Files:** `strategy/live_trader.py` (dead-zone expiry branch only, `~4913-4940`)
- **Depends on:** Task 1
- **Description:** replace `naked_bid = self._resolve_exit_bid(slug, mstate, naked_side)` with a `try/except RuntimeError` block that unpacks `naked_bid, _bid_src = self._resolve_exit_bid(mstate, naked_side)`; on `RuntimeError` log `"[%s] Dead-zone expiry exit for %s leg held: no executable bid in book or latch"` with `mstate.slug` and `naked_side`, then `return` **before** any state mutation (no `STOP_EXIT_PENDING`, no staged-stop clearing, no `_execute_stop_exit`). Leave the direct-bid path, the positivity check, and every mutation after it unchanged. Handle the file's local over-indentation of that block as-is — no reformatting.
- **Skill:** `incremental-implementation`
- **Verification:** `python -m pytest tests/test_live_trader.py -q` green, including the three Task 1 tests.

### [x] Task 3: [Debug] Regression sweep across the dead-zone contracts (S)
- **Files:** none (verification only)
- **Depends on:** Task 2
- **Description:** run the surrounding contracts that share this branch: the dead-zone parity suite (#229), the backtest engine's mirrored dead-zone behavior, and the docstring gate; confirm the success path still books one exit at the book bid (`test_naked_leg_dead_zone_force_exits_when_close`, `test_dead_zone_expiry_exit_fires_once_across_repeated_ticks`) and that `naked_leg_at_expiry="hold"` is untouched.
- **Skill:** `debugging-and-error-recovery`
- **Verification:** `python -m pytest tests/test_live_trader.py tests/test_dead_zone_parity.py tests/test_backtest_engine.py tests/test_docstrings.py -q` green.

## Dependency Graph
`Task 1 → Task 2 → Task 3` (linear: the repro gates the fix, the fix gates the sweep).

## Checkpoints
- **After Task 2:** the fix is green in isolation — one-line report, no approval pause (Mode A). ✅ 132 passed in `tests/test_live_trader.py`, including the three new tests.
- **After Task 3:** the branch is ready for Station IV. ✅ 308 passed across `tests/test_live_trader.py`, `tests/test_dead_zone_parity.py`, `tests/test_backtest_engine.py`, `tests/test_docstrings.py`.

## Build Record
- Commits: `6716404` (tests, red by design), `f7575ec` (fix). Files: `tests/test_live_trader.py`, `strategy/live_trader.py`, plus the plan and the quality bar.
- Verbatim pre-fix repro (Task 1): `TypeError: LiveTraderEngine._resolve_exit_bid() takes 3 positional arguments but 4 were given` at `strategy/live_trader.py:4934`.
- Out-of-scope finding recorded as `open` row N1 in `docs/issues/459-noticed-but-not-touching.md` (unguarded per-market update in `_tick_all_markets`); Station VI disposes of it.
