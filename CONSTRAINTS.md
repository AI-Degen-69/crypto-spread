# CONSTRAINTS.md — Issue #133: Increase Orders & Trades table height and view capacity in Live Cockpit

Binding while `feat/cockpit-table-height-133` is live. These are gates, not suggestions: a violation blocks the PR.

## 1. Zero regressions

- `python -m pytest -q` must end with **0 failures**, before and after.
  Baseline at planning time: 30 passed in `tests/test_orders_trades_table.py`, full suite green.
- Targeted gate, run on every task:
  `python -m pytest tests/test_orders_trades_table.py -q`
- Every new behavior ships with an automated test in the same commit.

## 2. Anti-cheat

- No `@pytest.mark.skip`, no `xfail`, no deleted or weakened assertions.
- Do not edit an existing test to lower standards or delete checks.
- Zero linter suppressions (`# noqa`).

## 3. Scope fence — UI / CSS / Cockpit tables only

- `server/osc_dash.py` and `tests/test_orders_trades_table.py` are the only codebase files this change may touch, plus the per-issue planning files (`CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`).
- Do **not** touch backend trading logic, order execution, `/api/live/*` endpoints, or book polling.
- Do **not** alter table column contracts (9 columns in Orders, 8 in Positions, 8 in Trades).
- Never commit anything under `run/` or `runs/`.

## 4. Visual & UI ergonomics gates

- **Minimum Height:** `#otPaneOrders`, `#otPanePositions`, and `#otPaneTrades` must provide at least 500px or 60vh vertical capacity in default/standard mode (from the old tight 280px/300px limit).
- **Sticky Headers:** `<thead>` column headers in all three panes must remain sticky (`position: sticky; top: 0`) and readable without content bleeding through when scrolling down hundreds of rows.
- **Row Density:** Row padding must be optimized (e.g. 6px-7px vertical padding) so at least 12–16 rows fit comfortably without feeling squished.
- **State Persistence:** Any view/height toggle must preserve operator preference across page reloads via `localStorage`.

## 5. Performance & Responsiveness

- Pure CSS and minimal DOM manipulation; zero network overhead.
- No layout shift or horizontal overflow regressions on standard viewports (1280px+).

## 6. Git discipline

- Feature branch: `feat/cockpit-table-height-133` off `master`.
- Atomic conventional commits, e.g.:
  `feat(cockpit): expand Orders & Trades table height and add sticky headers (#133)`
