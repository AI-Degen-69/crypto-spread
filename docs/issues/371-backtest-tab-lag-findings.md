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

**Dominant cost centre:** the client-side Chart.js instance leak (iteration 2,
below). The three server/poll centres above were real but individually small
(see measured numbers); the operator-visible "freeze every few seconds" came
from the browser: every sweep progress re-render leaked ~11 dead Chart.js
instances on detached canvases, and 1540 retained instances made each
subsequent render and Chart.js bookkeeping pass escalate until the main thread
gave the "Page Unresponsive" dialog. The fixes for all four shipped together
in this issue because each is independently verified in code or in-browser.

## Browser measurements (operator's machine, live session)

Measured via Chrome DevTools Protocol against `:5515` on the Backtest tab:

- **Plain backtest run (before any fix):** 250/250 rAF frames per 4–5 s, zero
  long tasks — the plain backtest path was never the problem.
- **Sweep run (before the chart-destroy fix):** long tasks of 50–140 ms at a
  ~500 ms cadence (85 in one 20 s window), rAF collapsed to 5–8 frames per 4 s
  — exactly the operator's "stuck and releases alternately" feel.
- **Chart.js instances before the fix:** 1543 total, 1540 dead
  (`canvas.isConnected === false`), 154 per canvas id `chartSweep_0..9`, only 3
  live.
- **After the fix (same sweep, full run to completion):** `Chart.instances`
  flat at 13 across three consecutive progress re-renders, 0 detached
  canvases, long tasks 4 in 8.4 s (max 62 ms), main thread responding in
  ≤6.3 ms (`setTimeout(0)` round-trip) *while the sweep was running*, sweep
  completes normally (1470 windows, took 1m 45s).

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
- Chart.js instance leak (iteration 2): sweep per-market charts are destroyed
  **by canvas element before** `grid.innerHTML = ''` detaches them — the old
  id-based lookup (`Chart.getChart(canvasId)`) cannot find a chart whose canvas
  is already detached, so every progress re-render kept the previous 10
  per-market instances alive. New `destroyChart(canvas)` helper handles
  detached canvases; summary charts were verified already-guarded.

## After measurements

To be appended after the fixes: same harness, same pinned file/filters —
poll latency idle vs during stream, progress gaps, total time. Before/after
go side by side here.

### Iteration 2 (chart-destroy fix) — measured after

Live sweep run after the fix, same machine:

- `Chart.instances`: 13 flat across repeated re-renders (was: growing by ~11
  per progress pass, 1543 accumulated).
- Detached canvases: 0 (was: 1540).
- Long tasks during a sweep: 4 in 8.4 s, max 62 ms (was: 85 in 20 s, max 140 ms).
- Main-thread `setTimeout(0)` round-trip during the sweep: avg 4.5 ms, max 6.3 ms.
- Sweep completed normally: 1470 windows, best overall `queue=10 (-$31.50)` —
  identical to the pre-fix result, confirming no behaviour change.
- Regression test: Node harness replays the real `renderSweepVisual` against a
  Chart.js mock whose id lookup fails on detached canvases; fails on the old
  code, passes on the new (`Chart.instances` stays at the number of visible
  canvases).

**Note on rAF sampling:** rAF frame counts read 0 during headless sampling
because the preview tab was occluded (browser-throttled), so main-thread
responsiveness is evidenced by the long-task and `setTimeout` measurements
instead. Numbers from an operator-visible tab are expected to match the plain
backtest profile (rAF near full rate).
