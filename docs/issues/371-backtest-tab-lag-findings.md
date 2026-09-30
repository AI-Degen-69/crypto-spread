# Findings — Issue #371: Backtest tab lags, freezes, and sometimes gets stuck

Branch: `i371/backtest-tab-lags-freezes-and-sometimes-gets-st`
Pattern: follows `docs/issues/221-gil-contention-findings.md`.
Measurement JSON: `docs/measurements/issue-371-backtest-tab.json`
(via `python -m scripts.measure_backtest_tab_cost`).

> Dataset scope: "All Files" replays `run/ticks` only, not the full golden
> corpus. Meaningful numbers require a machine holding the tick corpus —
> record the file and filter selection used alongside every measurement.

## Measured facts (baseline)

To be filled from the harness run on the operator's machine (the working
checkout does not hold the `run/ticks` corpus). The harness records, per run:

- `/api/oscillation` p50/p95/max latency idle vs during a streaming backtest,
  with and without a concurrent 3 s poll.
- Progress envelope cadence: gap p50/max, envelope byte size, points per
  envelope, total run time.

## Cost centre triage (code-verified before measurement)

Three cost centres, all verified in `server/osc_dash.py` before any fix:

1. **Global 3 s `/api/oscillation` poll** — `setInterval(tick, 3000)` fired with
   no tab/visibility awareness while a synchronous endpoint re-read windows and
   re-aggregated goals. Competes with the stream for the event loop and disk.
2. **Per-progress-batch client render** — every envelope (≤250 ms apart) rebuilt
   the provisional histogram (`Math.min(...vals)` spread over up to 2000 values),
   rewrote every metric card and called two Chart.js `update('none')` passes.
3. **Unbounded server accumulators** — `prog_pair_costs` / `prog_pair_edges`
   grew for the whole run and every 250 ms flush re-summed both lists: O(n)
   per flush, growing linearly with windows processed.

**Dominant cost centre:** to be named from the measured baseline numbers above
(measured, not assumed). The fixes for all three shipped together in this issue
because each is independently verified in code.

## Assumptions (to retire with data)

- Whether the freeze is main-thread-render dominated vs server-contention
  dominated on full-dataset runs — retire with a Chrome DevTools Performance
  profile alongside the harness numbers.
- 429 storms after a wedged run were the "stuck, nothing happens" symptom —
  confirmed by code reading (`runBacktest` retry 4×300 ms then silent give-up),
  not yet reproduced live.

## Changes shipped (summary)

- Polling guard: timer-driven poll skipped on Backtest tab / hidden document /
  in-flight; one immediate refresh when the operator returns.
- Constant-size progress accumulators: prefix-exact vs built-in `sum()`.
- Client render coalescing: ≤1 scheduled render per ~500 ms; single-pass min/max.
- Visible failure: terminal-event return, EOF watchdog, 429 message, pool-bound
  cleanup.

## After measurements

To be appended after the fixes: same harness, same pinned file/filters —
poll latency idle vs during stream, progress gaps, total time. Before/after
go side by side here.
