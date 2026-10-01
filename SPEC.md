# SPEC — Issue #371: Backtest tab lags, freezes, and sometimes gets stuck

Branch: `i371/backtest-tab-lags-freezes-and-sometimes-gets-st`

## Goal
Make the Backtest tab stay responsive during a streaming backtest, and make every abnormal
stream outcome (429, stall, wedged worker) fail visibly — without changing any backtest
result, parameter, engine behaviour, or displayed number.

## Acceptance criteria
1. A written measurement names the dominant cost centre among: the 3 s `/api/oscillation`
   poll, the per-progress-batch client render, and the server-side progress accumulators.
2. `/api/oscillation` is not polled while the Backtest tab is active or the document is
   hidden; Cockpit and Market Data keep their current refresh behaviour.
3. A full-dataset run leaves the Backtest tab interactive; the progress indicator updates at
   least once per second.
4. `prog_pair_costs` / `prog_pair_edges` state is constant-size; per-flush cost does not
   grow with windows processed.
5. A 429 or wedged run surfaces a visible, actionable error; the Run button never stays in
   "Simulating…" indefinitely.
6. Regression coverage added to `tests/test_osc_dash_integration.py`; existing tests in that
   file still pass.

## Numerical contract (binding)
The progress-envelope values `mean_pair_cost`, `mean_pair_edge_cents`, `pairs_above_settle`,
`pnl_sample_cents` must be byte-identical to today's. For every prefix of consumed values,
the accumulator's mean must equal `round(sum(prefix)/len(prefix), 4)` exactly (compensated
summation semantics of the interpreter's built-in `sum()` on Python ≥3.12; plain addition
below). Exclude only `None`; count `0.0`; separate denominators; return `None` for an empty
metric; equal window weights.

## Edge cases
- Superseded run (`window._btAbort !== ctl`) must never render or tear down a newer run's chart.
- Late cleanup of run A must not terminate run B's pool or clear run B's guards.
- EOF without a terminal event must fail visibly, not silently "succeed".
- A render callback throwing while handling `final` must be visible, not swallowed as
  "bad SSE payload".
- SSE comment heartbeat lines (`:` …) must be ignored by the client parser.

## Explicit out of scope
`backtest/engine.py`, `BacktestParams`, parameter registry, `/api/params/spec`, engine
simulation semantics, any displayed number, downsampling the provisional chart, caching
`_agg_goals`, verification-queue scheduling, blocking/sweep endpoint cleanup, Cockpit /
Market Data / Ticks / Sweep Visual tabs except the shared poll path.
