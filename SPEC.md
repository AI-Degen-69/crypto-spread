# SPEC: Issue #433 — Entry-side Touch Pair Gate with Disable Switch

## 1. Context & Goal
Issue #433 investigates whether to re-introduce an **entry-side touch pair gate** in the backtest engine (`backtest/engine.py`), with a disable switch (`<= 0`), drawing from `origin/feat/pair-cost-toggle`.

Previously, issue #227 removed the legacy entry-side pair cost gate because `max_pair_cost` was repurposed as a leg chase cap (structural limit capped at 1.00). However, quantitative research sweeps may wish to test or disable an entry-side touch filter (`touch = up_ask + dn_ask <= pair_cost_gate`).

Per the issue's designated default recommendation (**Option 1**), `pair_cost_gate` is re-introduced as a research/tuning knob in `BacktestParams`, with default `0.0` (disabled), reproducing today's behavior by default so that historical backtest results and baseline runs remain 100% bit-identical.

## 2. Requirements & Acceptance Criteria
1. **Dataclass Parameter**:
   - `BacktestParams.pair_cost_gate: float = 0.0`.
   - Default value is `0.0` (disabled).
2. **Gate Semantics in `_simulate_window`**:
   - If `params.pair_cost_gate <= 0`: gate is bypassed (`pair_cost_ok = True`).
   - If `params.pair_cost_gate > 0`: `touch = up_ask + dn_ask` when both `up_ask` and `dn_ask` are not None. If either is None, `touch = None`. `pair_cost_ok = (touch is None) or (touch <= params.pair_cost_gate)`.
   - If `not pair_cost_ok` and `not filled_up and not filled_down`: `continue` to next tick (entry postponed).
   - If already filled on either leg (`filled_up or filled_down`), gate check does not block fill/exit of open position.
3. **Validation in `__post_init__`**:
   - Must be a finite number (`int` or `float`), not `bool`.
   - Out-of-bounds: `pair_cost_gate > 2.00` is rejected with `ValueError` (as max possible touch in binary market is 2.00).
4. **Parameter Registry**:
   - Registered in `BacktestParams._PARAM_GROUPS["trading_knobs"]`.
   - Tuple format: `("pair_cost_gate", "Pair Cost Gate ($)", "Entry-side touch pair gate (0 disables)", "$", (0.50, 2.00), ("backtest",), "tuning")`.
   - Update `tests/test_param_registry.py` tuning set to include `"pair_cost_gate"`.
5. **No Regressions / Baseline Preservation**:
   - Any backtest run with default params (`pair_cost_gate=0.0`) must produce identical results to the current engine.
6. **Documentation**:
   - `docs/engine-decision-rules.md` updated to document `pair_cost_gate` as an opt-in research gate in backtest engine, distinct from `max_pair_cost`.

## 3. Non-Goals / Out of Scope
- Modifying `strategy/live_trader.py` (live/paper execution remains untouched per issue specification).
- Changing `max_pair_cost` semantics or bounds (it remains the structural chase cap <= 1.00).
- Enabling `pair_cost_gate` by default.
