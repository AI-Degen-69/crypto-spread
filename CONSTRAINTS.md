# Quality Guardrails & Constraints — Issue #367

## Scope & Functional Boundaries
- Add pure JavaScript presentation helpers (`sweepMarketName`, `sweepTokenColor`, `formatSweepMoneyTick`) in `server/osc_dash.py`.
- Render human market names (e.g., `Bitcoin Up or Down 5m`) in card titles, `aria-label`, detail dialog header, and "Best market" stat line.
- Apply brand colors from `ALL_COCKPIT_SERIES` to card titles and detail dialog heading.
- Format sweep chart Y-axis ticks without trailing `.00` (`$-200.00` → `$-200`, `$12.50` preserved, `-0` normalized to `$0`).
- No hardcoded hex literals in chart render functions or helper bodies.
- Do NOT change Python backend `series_labels` or API contract (`"05m BTC"`).

## Anti-Regression & Verification
- Targeted test suite: `python -m pytest tests/test_theme_tokens.py tests/test_osc_dash_integration.py -q`
- Node-based test harness for presentation helpers in `tests/test_theme_tokens.py`.
- No skipping/disabling tests or deleting assertions.
- No new runtime dependencies.
