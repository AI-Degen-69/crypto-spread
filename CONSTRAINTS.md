# CONSTRAINTS.md — Issue #155: Redesign Recent Windows table (entry-relative MAX UP/DOWN, RESULT pill, hyperlink SERIES cell)

Binding while `feat/recent-windows-redesign-155` is live. These are gates, not suggestions: a violation blocks the PR.

## 1. Zero regressions

- `python -m pytest -q` must end with **0 failures**, before and after.
- Targeted gates, run on every task:
  `python -m pytest tests/test_recent_windows_table.py tests/test_osc_dash_integration.py -q`
- Every new behavior ships with an automated test in the same commit.

## 2. Anti-cheat

- No `@pytest.mark.skip`, no `xfail`, no deleted or weakened assertions.
- Do not edit an existing test to lower standards or delete checks.
- Zero linter suppressions (`# noqa`).

## 3. Scope fence — Recent Windows table render only

- `server/osc_dash.py` (Recent Windows render block, currently ~lines 3817–3838) and the new `tests/test_recent_windows_table.py` are the only codebase files this change may touch, plus the per-issue planning files (`CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`).
- Do **not** change the window record schema, `classify_window` math, backtest, collector, or any `/api/*` endpoint.
- Do **not** touch OPEN UP/DOWN, CANDLE, or CLASS rendering except to keep row column order consistent.
- Never commit anything under `run/` or `runs/`.

## 4. UI correctness gates (from the issue's acceptance criteria)

- WINDOW and LINK columns are gone (7 columns total: Series, Open UP/DOWN, Max UP, Max DOWN, Candle, Class, Result).
- MAX UP renders `max_mid - start_mid`, MAX DOWN renders `start_mid - min_mid`; a window opened at $0.54 with a $0.55 high shows +$0.01, not +$0.05. Null inputs render `-`, never `NaN`.
- Highlight (existing `price-up` / `price-down` emphasis) applies only at delta ≥ $0.05.
- RESULT pill: green UP when `close_mid >= 0.50`, red DOWN otherwise, `-` on null — with text label, never color alone.
- SERIES cell shows `ASSET 5m/15m` + 24h `HH:MM-HH:MM` range from `start_ts`/`end_ts`, and the whole cell links to the Polymarket URL (`target="_blank" rel="noopener"`, URL passed through existing `esc()`).
- Reuse existing tokens/helpers only: `marketName()`, `fmtPrice()`, `esc()`, `pill()`/`clsPill()` styles, `price-up`/`price-down`, `tabular-nums`. No new color palette, no new dependencies.

## 5. Performance

- Client-side string render only; zero new network calls, zero new endpoints.
- No layout shift or horizontal overflow regressions on standard viewports (1280px+).

## 6. Git discipline

- Feature branch: `feat/recent-windows-redesign-155` off `master`.
- Atomic conventional commits, e.g.:
  `feat(dash): entry-relative MAX UP/DOWN and RESULT pill in Recent Windows (#155)`
