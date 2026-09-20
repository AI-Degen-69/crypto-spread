# Constraints — Issue #270: Flatten and standardize collapsible Backtest sections

## Scope
- Change Backtest presentation, collapse interaction, Sweep Visual chart presentation/expansion, canonical labels, and focused tests only.
- Preserve backtest calculations, API responses, timing semantics, sweep axes/data, stable DOM IDs, explicit-run behavior, selected-file state, live cockpit layout, and unrelated tabs.
- Use the existing Python/FastAPI dashboard, vanilla JavaScript, CSS tokens, Chart.js, and pytest setup.
- No new external dependencies.

## Correctness and compatibility
- All six major Backtest areas are peer sections with a clear heading and collapsible body.
- Every section heading is a `<button>` with matching `aria-expanded` and `aria-controls`; body visibility always matches the state.
- Reopening sections preserves controls, rendered chart instances/data, table contents, selected file, filters, and pagination.
- Aggregate and ten per-series Sweep Visual charts retain all tested values and existing best-result highlighting.
- Chart expansion is view-only: it must not call `/api/backtest` or `/api/backtest/sweep`, mutate parameter controls, or alter selected-file state.
- Per-Series Performance and Executed Windows Log rows/filter options render only duration-first canonical labels (`05m BTC` / `15m BTC`); old suffix labels are absent from those Backtest areas.
- Existing stable IDs, slug filter values, ordering, sorting, pagination, and API contracts remain unchanged.

## Accessibility and responsive UX
- Chart cards are keyboard focusable, have an accessible name, and activate with click, Enter, and Space.
- Expanded chart uses `role="dialog"` and an accessible label, contains a clear close button, supports Escape, has visible focus styling, moves focus into the dialog, and returns focus to the triggering card.
- Focus is not trapped outside the dialog; repeated open/close cycles do not leave stale focus handlers.
- No visible tick-label overlap or bar obstruction in small cards at 320px, 768px, 1024px, and 1440px supported widths.
- No horizontal overflow introduced by the flattened layout or modal.
- Color is not the only signal for best-result highlighting; text/labels remain available.

## Performance and runtime behavior
- No extra backtest/sweep network request on tab open, section toggle, chart click, modal open, or modal close.
- Detail rendering reuses the already-received sweep payload and avoids duplicate chart instances/listeners.
- Existing chart rendering remains responsive; modal open/close should complete without a visible blocking delay under normal local dashboard conditions.
- Browser verification must report zero console errors and no failed requests caused by the feature.

## Testing and anti-cheat
- Add focused HTML/JavaScript contract tests for section count/headings/ARIA wiring, stable IDs, no automatic simulation, canonical labels, chart-card expansion semantics, and modal accessibility hooks.
- Run `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` after implementation; development may use narrower `-k` selections.
- Browser verification is required for desktop and narrow layouts, chart overlap, expansion/close/Escape/focus return, canonical labels, and console/network cleanliness.
- Do not skip, weaken, delete, or suppress tests; do not remove assertions to make the suite pass.
- Do not add `@ts-ignore`, `eslint-disable`, `# noqa`, or equivalent suppression comments.
- Do not change API/data behavior to satisfy a presentation test.
