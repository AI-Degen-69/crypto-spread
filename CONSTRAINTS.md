# CONSTRAINTS.md — Issue #191: settle naked legs the book cannot mark

Binding while `fix/settle-unmarked-naked-191` is live.
Specification: `SPEC.md`. Task breakdown: `tasks/plan.md`.

## 1. Test suite integrity

- **Zero regressions.** `python -m pytest -q` must be green before and after
  every task. Current baseline: **856 passed**.
- **Anti-cheat.** No `skip`, `xfail`, deleted assertion, loosened tolerance, or
  silenced linter to make a task pass. If a test blocks the change, the change
  is wrong until argued otherwise in the PR.
- The two tests named in `SPEC.md` §5.1 and §5.2 must **fail on master** before
  the fix and pass after it. Write them first.

## 2. Bounded behaviour change

This issue deliberately changes simulated P&L, because today's number is wrong.
The change is bounded to exactly one code path:

- **Only the unmarked branch moves.** A window whose held leg has a real final
  `best_bid` must produce byte-identical `pnl_cents`, `fees_cents`,
  `exit_price`, and `settlement_mid` to master. A test must pin this.
- Pair-captured windows, stopped-out windows, and zero-fill windows are
  untouched.
- **No taker fee on a redemption.** A redeemed leg is never sold. The
  0.50-mark naked fee charged on entry stays; a second fee on the settled leg
  is a defect. Marked settlements (ladder stages 1-4) keep today's fee
  behaviour — see `SPEC.md` §7.
- **Abstain rather than guess.** No usable reference mid, or a reference mid of
  exactly 0.50, keeps the current `0.00c` and reports `unresolved`. Booking a
  coin flip is worse than booking nothing.

## 3. Live is the reference implementation

- `strategy/live_trader.py` is **not** modified. Its `_resolve_exit_bid` ladder
  (issue #160) is the contract this issue ports; the backtest moves toward
  live, never the reverse.
- The engine's stages 1-4 must match live's validity rules exactly, including
  the edges: a bid is valid only in `0.0 < bid <= 1.0`, and the binary
  complement is built from the opposite **ask**, never the opposite bid. Live
  documents why; reproducing the rule with different bounds is a defect even if
  the tests pass.
- No change to venue logic, order routing, or `scripts/collect_ticks.py` — a
  continuous capture is running and a restart loses the run.

## 4. One definition of the settlement rule

- `backtest/engine.py`, `research/sweeps/ev_lab.py`, and
  `research/sweeps/sim2.py` must not each carry their own copy of the
  redemption arithmetic. Issue #182 was caused by exactly this divergence
  between the audit and the simulator.
- After this issue the rule exists in one function, imported by the others. A
  second inline copy is a defect.
- Every settlement outcome is attributable: `settle_source` is recorded on
  `WindowResult` and exported, so a stale latched mark is distinguishable from
  a fresh quote in the results.

## 5. Scope fences

- No new external dependencies.
- No regeneration of `research/sweeps/*.json`. The stale marker stays; this
  issue appends to `research/sweeps/RESULTS-ARE-STALE.md`.
- No change to the sweep pipeline's headline numbers: `ev_lab.summarize`
  already applies the correction via `settle_correct=True`. This issue fixes
  the engine, not the summarizer. Applying the correction in both places would
  double-count.
- The fee-model question raised in `SPEC.md` §7 is **deferred**, not solved
  here. Changing `BacktestParams` semantics is out of scope.

## 6. Verification mode per task type

- `[Backend/Logic]` → `python -m pytest -q tests/test_backtest_engine.py tests/test_sweep_backtest.py`
- `[Research/Audit]` → `python research/sweeps/audit_settlement.py`. It must
  keep printing the raw uncorrected bias (the evidence in #191) **and** add a
  corrected line measured through the shared resolver, which must land at
  approximately 0. Replacing the raw line rather than adding to it is a defect.
- Full suite (`python -m pytest -q`) before every commit.
