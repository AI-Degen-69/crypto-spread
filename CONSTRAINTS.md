# CONSTRAINTS.md — Issue #200: Menu Collector status shows Could not query even while dashboard collector is running

Binding while `fix/collector-status-reliability-200` is live. These are gates, not suggestions: a violation blocks the PR.

## 1. Zero regressions

- `python -m pytest -q` must end with **0 failures**, before and after.
- Targeted gate, run on every task:
  `python -m pytest tests/test_osc_dash_integration.py -q`
- Every new behavior ships with an automated test or an explicit HTML/PS1-string assertion in the same commit.

## 2. Anti-cheat

- No `@pytest.mark.skip`, no `xfail`, no deleted or weakened assertions.
- Do not edit an existing test to lower standards or delete checks.
- Zero linter suppressions (`# noqa`).

## 3. Scope fence

- **May touch:** `server/osc_dash.py` (`api_collector_status` + `_count_lines_fast` call site, and `refreshCollectorStatus` catch block), `scripts/crypto-spread-menu.ps1` (Collector status query block, lines ~268-279), plus per-issue planning files (`CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`) and tests under `tests/`.
- **Do not touch:** collector engine capture logic (`scripts/collect_ticks.py`, tape handling, poll loop), CLOB / market-data paths, Trading Engine `/api/live/state` semantics, dashboard badge styling beyond the `catch` visibility fix, or any file under `run/` / `runs/`.
- Never commit anything under `run/` or `runs/`.

## 4. Correctness gates (from the issue's acceptance criteria)

- Starting the collector from the dashboard ("Start Polling") then running `scripts/crypto-spread-menu.ps1 status` shows Collector RUNNING (no `Could not query` warning) while collection is active.
- Menu Collector failure message includes the underlying reason (`$_`) — timeout vs connection-refused vs HTTP error are distinguishable, matching the Trading Engine query pattern at `scripts/crypto-spread-menu.ps1:257` / `:259`. The static-only string `Could not query /api/collector/status` without `$_` must be gone.
- `GET /api/collector/status` stays fast on a large tick file — no full O(n) scan per request when the file is large (≥20 MB heuristic, or TTL-cached). Must reuse the cheap-source policy already used in `api_ticks_manifest` / `_aggregate_ticks` (size-based estimate or cached count), not a new unbounded scan.
- `refreshCollectorStatus` in `server/osc_dash.py:3654-3712` must not silently hide failures (`catch{}` must log or surface the error; empty catch is disallowed).
- `python -m pytest tests/test_osc_dash_integration.py -q` passes.

## 5. Performance

- Large tick file (1 GB+) does not make `GET /api/collector/status` exceed the menu's timeout. Threshold: estimated or cached count for files ≥20 MB (same constant as `api_ticks_manifest`), and/or a short TTL cache (≤10s) for smaller files so repeated status polls do not re-scan.
- No new per-request unbounded I/O or subprocess.

## 6. Git discipline

- Feature branch: `fix/collector-status-reliability-200` off `master`.
- Atomic conventional commits, e.g.:
  `fix(dash): cheap tick count for collector status to avoid per-request full scan (#200)`
  `fix(menu): surface collector status failure reason and align timeout (#200)`
