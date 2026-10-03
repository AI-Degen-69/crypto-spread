# Quality Guardrails & Constraints - Issue #411

Move the Backtest Setup action row to the card's bottom-right and give the four
stop-loss thresholds a row of their own.

## 1. Zero Regressions
- `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` passes.
- `python -m pytest tests/test_osc_dash_integration.py -q -k "backtest or stop_loss or run_buttons"` passes.
- `python -m pytest tests/test_osc_dash_integration.py -q -k "stop or reset"` passes (Node harness tests skip if `node` is absent).
- No test is skipped, deleted, or weakened to make the change pass.

## 2. Layout Outcome (measurable)
- The five action controls (`btnRunSweep`, `btnStopBacktest`, `btnResetParams`, `btRuntimeEstBadge`, `btLastRunTime`) render **after** `bt-accordion` closes and **inside** `#btSecParametersBody`, in a container that is right-aligned (`justify-content:flex-end`).
- The four thresholds (`btExit5m`, `btExit15m`, `btExitBtc`, `btExitSol`) occupy one row of four, in that order, at desktop width.
- Share Size per Leg (`btSize`) sits on the row immediately above them.
- At the existing 900px breakpoint the threshold row falls back to two columns and the action row wraps right-aligned rather than gaining a new breakpoint.

## 3. Behaviour Invariance (hard bar)
- **No** `id`, `data-param`, `onclick`, `name`, or default `value` changes anywhere.
- `btControlValues()`, `btControlQuery()`, `resetBtParams()` and the request payload are byte-identical before and after.
- The backtest request still carries the same values - layout only, zero semantic change.

## 4. Anti-Cheat & Code Standards
- No new external libraries or runtime dependencies.
- No new inline `style=` for the moved row - the alignment lives in the `<style>` block.
- Only `server/osc_dash.py` (markup + CSS in `#tab-backtest`) and `tests/test_osc_dash_integration.py` are touched.
- Must NOT be modified: `btControlValues()`, `btControlQuery()`, `resetBtParams()`, `updateBtStopVisibility()`, `stopBacktestRun()`, any backend endpoint, the Cockpit mirror tab, the Sweep Visual / Per-Series / Log sections, the results tiles, or `btnRunSweepVisual`.
- Do not add, remove, or reorder the four sub-sections themselves.
- Use `docs/glossary.md` names in comments ("the backtest tab", "the trading engine"). Never write "live" as a name for the trading engine.
- This `CONSTRAINTS.md` is per-issue working state and goes stale once #411 merges; it is not architecture. --' Issue #411
