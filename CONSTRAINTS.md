# CONSTRAINTS.md — Issue #201: Group backtest stop-loss params under an on/off toggle

Binding while `feat/backtest-stop-loss-toggle-201` is live. These are gates, not
suggestions: a violation blocks the PR.

## 1. Zero regressions

- `python -m pytest -q` must end with **0 failures**, before and after.
- Targeted gate, run on every task:
  `python -m pytest tests/test_osc_dash_integration.py -q`
- Every new behavior ships with an HTML-string assertion in
  `tests/test_osc_dash_integration.py` in the same commit.

## 2. Anti-cheat

- No `@pytest.mark.skip`, no `xfail`, no deleted or weakened assertions.
- Do not edit an existing test to lower standards or delete checks.
- Do not silence a failing registry/bounds test by removing `data-param`.

## 3. Scope fence

**Widened by the operator on 2026-09-16, after the first three commits:** the
same grouping was requested for the Live Cockpit, and `applyWinningConfig()` was
told to state hold-to-settle via the toggle instead of faking it with 0.49/0.50
stops. The original backtest-only fence below no longer binds those two areas.

- `server/osc_dash.py` backtest tab Operator Controls, the Cockpit stop-loss
  control (`cockpitStopLossEnabled` and its threshold), and their JS.
- **Forbidden:** `backtest/engine.py` semantics of `stop_loss_enabled`,
  threshold bounds, any other parameter group, any new dependency.
- Backend `stop_loss_enabled` passthrough (`server/osc_dash.py:651,718`) stays
  byte-identical — this is a front-end grouping task.

## 4. Behavioral gates

- Toggle Off → the four threshold inputs are hidden **and** `disabled`, and the
  `/api/backtest` request carries `stop_loss_enabled=0`.
- Toggle On → fields visible, enabled, request carries `stop_loss_enabled=1`.
- Default page state = On (matches today's `<option value="1" selected>`).
- Toggle markup reuses the existing `toggle-wrap` / `toggle-switch` /
  `toggle-slider` classes and the ON/OFF label pattern of
  `togglePairCostInput()` — no new CSS classes.
- `resetBtParams()` and `applyWinningConfig()` leave no half-applied state: if
  either touches the toggle it must call the toggle handler, as they already do
  for `btPairCostEnabled`.
- The control keeps `id="btStopLossEnabled"` and `data-param="stop_loss_enabled"`
  so `applyParamSpec()` surface detection and
  `test_every_registry_control_has_an_id_the_surface_detection_understands`
  still pass.

## 5. Performance

- No new network calls, no new timers, no per-keystroke work. Toggle handler is
  O(4) DOM writes.

## 6. Git discipline

- Feature branch: `feat/backtest-stop-loss-toggle-201` off `master`.
- Atomic conventional commits, e.g.:
  `feat(dash): group backtest stop-loss thresholds under one on/off toggle (#201)`
