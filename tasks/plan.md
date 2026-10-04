# Branch: i433/feat-backtest-decide-whether-to-re-add-an-entry | Issue: #433

## CodeRabbit / Prior Work Intake
- **CodeRabbit Plan**: No plan comment present on issue #433.
- **`origin/feat/pair-cost-toggle` Intake**:
  - Adopted: `pair_cost_gate` parameter concept, `<= 0` disable condition, touch calculation `touch = up_ask + dn_ask <= pair_cost_gate`.
  - Rejected: Default `1.05` rejected in favor of `0.0` (disabled) to preserve exact baseline reproducibility (Option 1). Rejected `fill_model = "cross"` which was in the same old commit, as issue #226 permanently unified the fill rule into `book_math.resting_bid_filled`.
- **Open Questions Resolution**:
  - Issue question: Option 1 vs 2 vs 3.
  - Resolution: Option 1 selected per issue recommendation (smallest change preserving #227 while unlocking research sweeps, zero-risk default).

## Improvement Proposal (Grounding & Classification)
- **Evidence**: `tests/test_backtest_engine.py:1363-1370` checks `gate = src.index("if not queue_ok:")` and expects exactly `assert continues == ["not filled_up and not filled_down"]`.
- **Proposal**: Place `pair_cost_ok` evaluation immediately above `if not queue_ok:`, ensuring the gate exit check for `pair_cost_ok` does not disturb the sliced AST/source range verified by `test_naked_thr_checked_on_gate_failure`.
- **Classification**: Simplification / edge-case hardening (adopted by default).

---

## Tasks

### Task 1: [Backend/Logic] [S] Add `pair_cost_gate` to `BacktestParams`, validation, and parameter registry [x]
- **Depends on**: None
- **Files**: `backtest/engine.py`, `tests/test_param_registry.py`
- **Helper Skill**: `api-and-interface-design`, `test-driven-development`
- **Description**:
  - Add `pair_cost_gate: float = 0.0` to `BacktestParams`.
  - Add validation in `__post_init__`: reject booleans, non-finite values, and `pair_cost_gate > 2.00`.
  - Register `pair_cost_gate` in `_PARAM_GROUPS["trading_knobs"]` with 7-tuple: `("pair_cost_gate", "Pair Cost Gate (c)", "Entry-side touch pair gate (0 disables)", "$", (0.50, 2.00), ("backtest",), "tuning")`.
  - Update `tuning` set in `tests/test_param_registry.py` to include `"pair_cost_gate"`.
- **Verification**: `python -m pytest tests/test_param_registry.py -q`

---

### Task 2: [Backend/Logic] [S] Implement touch pair gate in `_simulate_window` & add engine tests
- **Depends on**: Task 1
- **Files**: `backtest/engine.py`, `tests/test_backtest_engine.py`
- **Helper Skill**: `test-driven-development`, `debugging-and-error-recovery`
- **Description**:
  - In `_simulate_window`: calculate `pair_cost_ok`:
    If `params.pair_cost_gate <= 0`: `True`.
    Else: `touch = (up_ask + dn_ask)` if both present else None; `(touch is None) or (touch <= params.pair_cost_gate)`.
    If `not pair_cost_ok` and `not filled_up and not filled_down`: `continue`.
  - Ensure placement preserves `test_naked_thr_checked_on_gate_failure`.
  - In `tests/test_backtest_engine.py`:
    - Add test that default `pair_cost_gate=0.0` reproduces current behavior exactly.
    - Add test that `pair_cost_gate <= 0` disables gate even on wide touch.
    - Add test that `pair_cost_gate > 0` blocks entry when touch exceeds threshold.
    - Add test that `__post_init__` rejects invalid values (bool, non-finite, > 2.0).
- **Verification**: `python -m pytest tests/test_backtest_engine.py -q`

---

### Task 3: [Backend/Docs] [S] Parameter sweep compatibility, doc update & targeted test verification
- **Depends on**: Task 2
- **Files**: `scripts/sweep_backtest.py`, `docs/engine-decision-rules.md`, `tests/test_sweep_backtest.py`
- **Helper Skill**: `documentation-and-adrs`, `test-driven-development`
- **Description**:
  - Check `scripts/sweep_backtest.py` compatibility: ensure any grid or axis sweep continues working without regressions.
  - Update `docs/engine-decision-rules.md` (§4 and §Parameter classes) clarifying `pair_cost_gate` as an opt-in research knob (default `0.0`), distinguishing it from the structural `max_pair_cost` chase cap.
  - Run all targeted test suites.
- **Verification**:
  - `python -m pytest tests/test_backtest_engine.py -q`
  - `python -m pytest tests/test_param_registry.py -q`
  - `python -m pytest tests/test_sweep_backtest.py -q`
