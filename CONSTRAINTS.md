# Quality Guardrails & Constraints — Issue #398

## 1. Zero Regressions
- Targeted tests covering the modified modules must pass: `python -m pytest tests/test_osc_dash_integration.py -k "render_summary or oscillation" -q` (and all related targeted test suites).
- Zero syntax or runtime exceptions on dashboard UI initialization.

## 2. Zero Line Styling Consistency
- All four oscillation tab charts (`cPerAsset`, `cHist`, `cStart`, `cPair`) must configure consistent zero line handling by registering `sweepZeroLinePlugin()` and/or applying unified y-grid styling.
- Zero lines must consistently display with gold color (`theme.gold`), dashed pattern `[6, 4]`, and width `2` (or plugin default 1.5/2).
- Non-Cartesian charts (doughnut chart `cStart`) must safely tolerate plugin registration without throwing scale lookup errors.

## 3. Anti-Cheat & Code Standards
- No test skipping or deletion.
- No new external libraries or runtime dependencies.
- Changes isolated to oscillation charts in `server/osc_dash.py` and integration tests.
