# CONSTRAINTS — Issue #254: Unify dashboard theme into single design-token source (CSS vars + Chart.js)

## Scope Lock
1. **Single Source of Truth**: All palette colors in the dashboard (`FULL_APP_HTML` in `server/osc_dash.py`) must be defined exclusively in `:root` CSS variables.
2. **Zero Color Redesign**: Visual identity remains 100% identical (dark slate, teal for up, coral for down, gold for warning, project blue, IBM Plex / Space Grotesk fonts, RTL Hebrew layout).
3. **No Hardcoded Hexes in CSS/Chart.js**: Replace all hardcoded hex literals in CSS rules (`.tbl td`, `.btn-primary`, `.spinner`, `.thinking-dots`) and Chart.js configs (`chartEquity`, `chartPnlHist`, `cPerAsset`, `cHist`, `cStart`, `cPair`) with tokens.
4. **No Logic or Backend Changes**: Python API routes, execution engine, collector, and backtest logic are untouched.
5. **No External Dependencies**: Vanilla JS / DOM `getComputedStyle`, no new npm packages or script CDN additions.

## Quality Guardrails
6. **Targeted Test Gate**:
   - `python -m pytest tests/test_osc_dash_integration.py -q` must pass with 0 regressions.
   - `python -m pytest tests/test_theme_tokens.py -q` must pass, verifying token definitions and absence of hardcoded hexes in styles and chart configs.
7. **Anti-Cheat**:
   - No disabling, skipping, or weakening tests.
   - Zero hardcoded hex/rgba color literals outside `:root` and coin asset branding.
