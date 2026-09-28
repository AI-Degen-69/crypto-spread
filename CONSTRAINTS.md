# Quality Guardrails & Constraints — Issue #332

## 1. Zero Regressions & Verification
- Targeted test suites covering the modified files must pass:
  ```powershell
  python -m pytest tests/test_theme_tokens.py tests/test_osc_dash_integration.py -q
  ```
- All existing contract assertions in `tests/test_theme_tokens.py` (including `id="chartSweepAgg"`, `id="btSweepGrid"`, `parsing: false`, `beginAtZero: true`, `afterBuildTicks`, `best_overall`, `best_market`, `series_labels`, `★ BEST MARKET`, etc.) must remain intact.

## 2. Zero New Dependencies
- Do NOT add any Chart.js plugins (e.g. annotation plugins) or new CDN scripts.
- Use only native Chart.js 4.4.0 scriptable callbacks and existing dashboard theme tokens (`theme.up`, `theme.down`, `theme.gold`, `theme.dim`, `theme.line`).

## 3. Anti-Cheat & Code Bar
- No skipping, commenting out, or weakening existing tests or assertions.
- No hardcoded hex values in chart logic; use `getThemeTokens()` properties (`theme.up`, `theme.down`, `theme.gold`, etc.) to maintain light/dark theme support.
- Neutral/zero or non-finite values must resolve safely without throwing or distorting colors.

## 4. UI Stability & Performance
- Aggregate and per-market Sweep Visual cards must be compact (~90px and ~70px canvas heights, tighter grid `minmax(160px,1fr)`) without breaking expanded detail dialog functionality (`openBtChartDetail`).
- Sweep endpoint `/api/backtest/sweep`, `SWEEP_AXES`, and data payload structures remain completely untouched.
