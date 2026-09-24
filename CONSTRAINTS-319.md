# CONSTRAINTS — Issue #319: Jungle King dashboard tab

## Zero regressions
- Targeted suites covering modified files must pass locally:
  - `python -m pytest tests/test_osc_dash_integration.py -q`
  - `python -m pytest tests/test_param_registry.py -q`
- New behavior requires tests (the endpoint and the tab contract get tests).
- Full-repo sweeps stay with CI on push (per `AGENTS.md`, local full runs are
  forbidden — 932 tests, ~96s).

## Read-only guarantee
- No contract change: `/api/params/spec`, `BacktestParams`, and the params-hash
  contract are untouched. Verify with the targeted suites above.
- No mutation path from the tab: no POST/PUT from the new UI, no calls into
  `/api/backtest`, `/api/collector/*`, or `/api/live/*`.
- The manifest file `research/jungle-king/param_ranges.json` is never written.

## Performance thresholds
- `GET /api/jungle-king` serves from a single small file read (~368 lines) —
  budget: well under 50 ms cold; no cache machinery, no background threads.
- The tab's first render issues exactly one fetch (no per-parameter requests).

## Anti-cheat
- Strictly forbid skipping/disabling tests, deleting assertions, or
  suppressing linters to make checks pass.
- No `pytest.skip` on the new tests; no try/except that swallows failures the
  acceptance criteria exist to catch.

## Dependencies
- No new external dependencies. Stack stays: FastAPI + `HTMLResponse`/JSON in
  `server/osc_dash.py`, vanilla JS in `FULL_APP_HTML` (no frameworks, no
  charting libs — no charts are in scope).

## Naming (docs/glossary.md)
- UI labels follow the glossary: "parameter" = tuning knob; structural limits
  and execution assumptions are labelled as such. Never label the tab or its
  content "live" — that word is not a name for the trading engine, and this tab
  does not touch the engine at all.
