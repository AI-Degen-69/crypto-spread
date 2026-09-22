# CONSTRAINTS.md — Issue #292: Golden dataset card in Tick Files

Branch: `i292/golden-dataset-card-tick-files` · Size: Standard · Type: Code

## Hard boundaries
1. **Zero regressions:** `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
   must pass with the new golden tests. Never the full suite locally (CI gates it).
2. **Read-only certification:** the endpoint renders certification state — it never creates,
   modifies, quarantines, or re-streams golden data. Dashboard load must not scan day files
   (verify caches only).
3. **Computation lives in the backend:** the frontend renders `checks[]` from
   `/api/ticks/golden` verbatim (measured / required / ok / n-N fields) — no gate logic in JS.
4. **No new dependencies.** stdlib + existing FastAPI only.
5. **Anti-cheat:** no skipping/disabling tests, no deleted assertions, no suppressed linters.
6. **Absent ≠ error:** missing `run/ticks/golden/` returns 200 with an explicit `absent` state
   and an unchecked checklist — never an error state or stack trace.
7. **Scope lock:** no golden capture/certification (#281), no verify-engine/threshold changes,
   no pristine view, no backtest/collector changes, no new sidebar tab.
8. **Security:** golden manifest JSON parsed defensively (malformed → `absent`-style degraded
   state with reason, never a 500); no path handling beyond reading the fixed `golden/` dir.
