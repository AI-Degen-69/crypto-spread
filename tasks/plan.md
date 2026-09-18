# Task Plan — Issue #254: Unify dashboard theme into single design-token source (CSS vars + Chart.js)

**Size tier:** Small — focused refactoring of frontend design tokens and chart palette references within `server/osc_dash.py`'s `FULL_APP_HTML`.
**Task type:** Design/UI (CSS variables, Chart.js palettes, theme token centralization).

## Context & Problem
- The dashboard embedded in `server/osc_dash.py` (`FULL_APP_HTML`) defines a dark theme palette in `:root` CSS variables (`--bg`, `--panel`, `--panel2`, `--line`, `--line-hi`, `--tx`, `--dim`, `--faint`, `--up`, `--down`, `--gold`, `--proj`).
- However, multiple CSS rules (`.tbl td`, `.btn-primary:hover`, `.spinner`, `.thinking-dots`, form input placeholders) and Chart.js chart configs (`chartEquity`, `chartPnlHist`, `cPerAsset`, `cHist`, `cStart`, `cPair`) hardcode raw hex literals (`#1a2029`, `#0a0d12`, `#2bb5a2`, `#33c9b5`, `#f0684d`, `#8792a6`, `#232a35`, etc.).
- When CSS tokens in `:root` are adjusted, charts and these styled components do not reflect the changes, resulting in visual inconsistency and theme drift.

## Proposed Improvement (Adopted by default)
- Add a lightweight `getThemeToken(name)` helper in the frontend script that reads `getComputedStyle(document.documentElement).getPropertyValue(name).trim()`. This helper will be used across all Chart.js canvas charts as well as dynamic SVG renders (cockpit timeline chart), ensuring 100% theme synchronization across the entire application with zero overhead.

## Tasks

- [x] **TASK-1 [Design/UI]**: Promote missing tokens to `:root` and clean up hardcoded hexes in CSS rules
  - Target: `server/osc_dash.py` (`FULL_APP_HTML` `<style>` block)
  - What is built:
    - Add `--up-hi: #2bb5a2;` and `--line-dark: #1a2029;` to `:root`.
    - Replace `.tbl td` border `#1a2029` with `var(--line-dark)`.
    - Replace `.btn-primary` color `#0a0d12` with `var(--bg)`.
    - Replace `.btn-primary:hover` and `.btn-primary.thinking` `#2bb5a2` with `var(--up-hi)`.
    - Replace `.spinner` and `.thinking-dots` `#0a0d12` with `var(--bg)`.
    - Clean up form input placeholder / validation hardcoded fallback hexes (`#78879b`, `#f0684d`).
  - Helper skill: `make-interfaces-feel-better`
  - Verify: Inspection of `<style>` in `FULL_APP_HTML` contains zero hardcoded hex literals outside `:root`.

- [x] **TASK-2 [Design/UI]**: Centralize theme token reading in JS and bind Chart.js + SVG configs
  - Target: `server/osc_dash.py` (`FULL_APP_HTML` `<script>` block)
  - What is built:
    - Define `getThemeToken(name)` and cached `THEME` object in frontend script.
    - Update `chartEquity` config to use `THEME.up`, `THEME.down`, `THEME.dim`, `THEME.line`.
    - Update `chartPnlHist` config to use `THEME.down`, `THEME.up`, `THEME.dim`, `THEME.line`.
    - Update summary charts (`cPerAsset`, `cHist`, `cStart`, `cPair`) to use `THEME` tokens.
    - Update SVG cockpit chart line/dot stroke references to use theme tokens.
  - Helper skill: `make-interfaces-feel-better`
  - Verify: All Chart.js configs reference `THEME` / `getThemeToken` instead of raw hex literals.

- [x] **TASK-3 [QA/TDD]**: Create automated theme token test suite
  - Target: `tests/test_theme_tokens.py`
  - What is built:
    - Test that `:root` defines all necessary color tokens (`--bg`, `--panel`, `--line`, `--up`, `--up-hi`, `--down`, `--line-dark`, `--gold`, `--proj`).
    - Test that CSS component rules and Chart.js instantiation blocks inside `FULL_APP_HTML` do not contain hardcoded hex literals.
    - Verify `FULL_APP_HTML` correctly exposes `getThemeToken`.
  - Helper skill: `python-testing`
  - Verify: `python -m pytest tests/test_theme_tokens.py -q` passes (<1s).

- [x] **TASK-4 [Regression]**: Run dashboard integration tests
  - Target: `tests/test_osc_dash_integration.py`
  - What is built:
    - Ensure zero regressions in HTML delivery, endpoint responses, and cockpit state.
  - Helper skill: `python-testing`
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q` passes.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | Regex check for zero hex literals outside `:root` in `<style>` |
| TASK-2 | Inspection of Chart.js scripts + browser preview check |
| TASK-3 | `python -m pytest tests/test_theme_tokens.py -q` |
| TASK-4 | `python -m pytest tests/test_osc_dash_integration.py -q` |

## Post-build gates (Station IV)
- `python -m pytest tests/test_theme_tokens.py tests/test_osc_dash_integration.py -q` passes in <15s with 0 errors.
