# Plan — Issue #371: Backtest tab lags, freezes, and sometimes gets stuck

Branch: i371/backtest-tab-lags-freezes-and-sometimes-gets-st | Issue: #371
Size: Standard | Type: Code (Performance/UX) | Verification: targeted pytest + Node harnesses

## Intake (CodeRabbit plan, recorded once)
- **Adopted:** 5-phase skeleton, verified seams, sum()-algorithm accumulator contract,
  opt-in stream-reader options, pool-bound cleanup design, Phase 5 test matrix.
- **Rejected:** the 15-task split (over-split) — merged into 5 atomic tasks.
- **[UNVERIFIED]→resolved:** "no keep-alive on /api/backtest/stream" — ping confirmed only
  on the live-stream generator (osc_dash.py:3730); backtest generator verified at build.
- Citation spot-check: all cited paths/symbols/line numbers matched the live checkout.

## Open questions (needs-answers) — resolved
1. Uncommitted diff → keep & extend (operator approved); guard moves from `tick()` into
   the timer wrapper `pollTick()`.
2. Freeze location → measured in Task 1 (all three cost centres independently code-verified).
3. 429 → visible actionable message; no longer silent give-up.
4. Responsiveness target → tab interactive throughout; progress ≥1/s.

---

### Task 1 — Baseline measurement + findings doc
- Size: M | Tag: [Research/Perf] | Depends on: —
- Files: `scripts/measure_backtest_tab_cost.py` (new), `docs/measurements/issue-371-backtest-tab.json` (new),
  `docs/issues/371-backtest-tab-lag-findings.md` (new).
- Build: harness modelled on `scripts/measure_gil_contention.py` — idle vs concurrent-backtest
  `/api/oscillation` latency; `/api/backtest/stream` progress gaps/envelope sizes/total time
  with and without a 3 s poll; findings doc naming the dominant cost centre (pattern of
  `docs/issues/221-gil-contention-findings.md`); before/after numbers appended after Task 4.
- Verify: `python -m scripts.measure_backtest_tab_cost --help` runs offline-safe; harness
  records dataset scope; manual run only on a machine with `run/ticks`.

### Task 2 — Polling guard (frontend)
- Size: M | Tag: [Frontend/Logic] | Depends on: —
- Files: `server/osc_dash.py` (switchTab ~6452, setInterval ~12089, tick ~6824).
- Build: keep `currentActiveTab` from the working-tree diff; add `pollTick()` wrapper with
  `document.hidden` + Backtest-tab + in-flight guards; pass it to `setInterval`; move the
  hidden/tab guard out of `tick()`; staleness flag → one immediate `tick()` on leaving
  Backtest and on `visibilitychange` returning visible outside Backtest.
- Verify: Node harness in `tests/test_osc_dash_integration.py` (Task 5).

### Task 3 — Constant-size progress accumulators (server)
- Size: M | Tag: [Backend/Logic] | Depends on: —
- Files: `server/osc_dash.py` (near `_run_backtest_simulation_worker` 1677, `_flush_progress` 1757).
- Build: helper class holding P&L deque(maxlen=2000) (completion order, 2-dec), count +
  compensated running sum for pair cost/edge, count of `round(c,4) > 1.00`; prefix-exact
  vs `round(sum(prefix)/len(prefix), 4)` on Py≥3.12, plain-add below; excludes only `None`;
  `None` result for empty metric; inspectable state size. `_flush_progress` reads from it;
  payload keys/order unchanged; flush triggers, `put_nowait`/`_disable_progress`, final
  aggregation, zero-window return untouched.
- Verify: oracle unit tests + worker-level last-envelope ≡ final-result (Task 5).

### Task 4 — Visible failure & pool-bound cleanup (frontend + backend)
- Size: L | Tag: [Frontend/Logic + Backend/Logic] | Depends on: Task 2, Task 3
- Files: `server/osc_dash.py` (consumeBacktestStream 7811, runBacktest 7844,
  markBacktestFailed 7601, btAppendProvisionalPoints 7737, btUpdateProvisionalHist 7703,
  _terminate_backtest_pool 1631, /api/backtest/stream generator ~2800-3029).
- Build:
  - Render coalescing: `btAppendProvisionalPoints` pushes chart points + stores latest
    envelope; cards/elapsed/hist/`update('none')` move into a ~500 ms-gated scheduled render
    (`setTimeout` gate + `requestAnimationFrame`), guarded by `window._btAbort !== ctl`,
    cancelled before `btDestroyProvisionalChart()` on final/error/abort/finally.
  - `btUpdateProvisionalHist`: single-pass min/max; buckets/label unchanged.
  - `consumeBacktestStream(res, ctl, onEvent, opts)`: opt-in terminal return after
    `final`/`error`; EOF without terminal → `markBacktestFailed("Stream ended without a
    result")`; callback exceptions surfaced; non-abort transport errors shown; `AbortError`
    silent; ignore SSE comment lines; opt-in inactivity watchdog (no bytes ⇒ cancel+abort+
    visible stall message).
  - Heartbeat: confirm/add periodic SSE comment from the backtest event generator.
  - 429: keep short retry, no sleep after last attempt, show server `error` text or fixed
    actionable message; `markBacktestFailed` renders message text in a visible element.
  - `_terminate_backtest_pool(pool=None)`: terminate passed pool; clear `_BACKTEST_POOL`
    only if still that pool; stream cleanup uses the run's own executor; `release_guards()`
    in try/finally; guards released on init-time cancellation/BaseException.
- Verify: Node lifecycle tests + extended disconnect test (Task 5).

### Task 5 — Regression tests + verification
- Size: L | Tag: [Tests] | Depends on: Task 2, Task 3, Task 4
- Files: `tests/test_osc_dash_integration.py`.
- Build: Node polling-guard tests (pattern `test_jungle_king_client_coalesces_pending_loads`:
  suppression on Backtest/hidden, polling on Market Data, no overlap, one refresh on return);
  accumulator oracle tests (>2000 heterogeneous values incl. `None`, `0.0`, `1.00004/1.00006`,
  rounding boundaries; bounded state; worker-level equivalence); stream-lifecycle Node tests
  (final + never-resolving read, EOF without terminal, 4×429 visible + no final sleep,
  watchdog stall, render coalescing one-update-per-window-gate); extend
  `test_backtest_stream_disconnect_releases_guards_immediately` with run-A/B pool ownership.
- Verify: `python -m pytest tests/test_osc_dash_integration.py -q` only; CI gates the rest.
  Manual full-dataset run on :5515 where corpus exists (operator machine).

## Checkpoints
- After Task 3: server-side constant memory proven by tests (one-line progress report).
- After Task 5: full targeted suite green; findings doc carries before/after numbers.

## Sub-issues
Standard-size work → one sub-issue per task with native blocked-by edges (1→2→3→4→5).
