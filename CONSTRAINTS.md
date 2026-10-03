# Quality Guardrails & Constraints — Issue #399

## 1. Zero Regressions
- Targeted tests covering the modified modules must pass: `python -m pytest tests/test_theme_tokens.py -q` (and `tests/test_osc_dash_integration.py`).
- No breaking changes to existing chart scales, axis configurations, or layout.

## 2. Tick Label Formatting Bar
- Whole dollar amounts on Y-axis (and X-axis for P&L distribution where applicable) must display without decimals (e.g. `$2`, `$0`, `$-5`).
- Fractional amounts must retain 2 decimal places (e.g. `$2.50`, `$-1.25`).
- Zero amounts must cleanly format as `$0` (no `$0.00` or `$-0.00`).
- Consistent helper usage (`formatSweepMoneyTick` or unified money tick helper) without duplicated formatting logic across chart configurations.

## 3. Anti-Cheat & Code Standards
- No test skipping or deletion.
- No new external libraries or runtime dependencies.
- Changes isolated to tick formatting callbacks in `server/osc_dash.py` and regression tests in `tests/test_theme_tokens.py` / `tests/test_osc_dash_integration.py`.
