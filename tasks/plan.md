Branch: i364/expose-pair-cost-merge-edge | Issue: #364

# Execution Plan — Issue #364: Expose Pair Cost and Merge Edge

## CodeRabbit Plan Intake
- Adopted: Standard 4-task vertical decomposition aligned with issue AC.
- Rejected: N/A (no CodeRabbit response plan comment present on issue).
- Unverified: N/A.

## Task Breakdown

### Task 1: Add Shared Realized Pair Edge Math Helper
- **Task ID:** T1
- **Size:** S
- **Domain Tag:** [Backend/Logic]
- **Target Files:** `strategy/book_math.py`, `tests/test_backtest_engine.py`
- **Description:** Implement `realized_pair_edge_cents(entry_up, entry_down, merge_gas_usd=0.0, quote_shares=50)` in `strategy/book_math.py`. Add unit tests asserting it returns exact realized cents per share including amortized gas.
- **Assigned Helper Skill:** `test-driven-development`
- **Depends on:** None
- **Verification:** `python -m pytest tests/test_backtest_engine.py -k "test_realized_pair_edge" -q`

### Task 2: Update Engine WindowResult & Replay Aggregation
- **Task ID:** T2
- **Size:** M
- **Domain Tag:** [Backend/Logic]
- **Target Files:** `backtest/engine.py`
- **Description:** Update `WindowResult` to store `first_pair_cost`, `mean_pair_edge_cents`, `worst_pair_edge_cents`, and `pair_pnl_cents`. Use `realized_pair_edge_cents` in `_simulate_window` pair completion block. Expose new fields in `trades_sample` and aggregate summaries (`overall`, `per_series`, `per_duration`), adding `pair_rate_entered`, `mean_pair_cost`, `mean_pair_edge_cents`, `total_pair_pnl_cents`.
- **Assigned Helper Skill:** `incremental-implementation`
- **Depends on:** T1
- **Verification:** `python -m pytest tests/test_backtest_engine.py -q`

### Task 3: Mirror Telemetry in Dashboard Stream Summaries
- **Task ID:** T3
- **Size:** M
- **Domain Tag:** [Backend/Logic]
- **Target Files:** `server/osc_dash.py`
- **Description:** Update `/api/backtest` summary building and `trades_sample` rows in `server/osc_dash.py` to calculate and return identical pair-economics fields and keys as `backtest/engine.py`.
- **Assigned Helper Skill:** `incremental-implementation`
- **Depends on:** T2
- **Verification:** `python -m pytest tests/test_osc_dash_integration.py -k "backtest" -q`

### Task 4: Comprehensive Parity & Reconciliation Tests
- **Task ID:** T4
- **Size:** S
- **Domain Tag:** [Debug/Verification]
- **Target Files:** `tests/test_backtest_engine.py`
- **Description:** Add unit tests validating: 1) Engine P&L equality on single-merge windows, 2) Row P&L reconcilability on multi-event windows, 3) Strict field and aggregate parity between `backtest/engine.py` and `server/osc_dash.py`.
- **Assigned Helper Skill:** `test-driven-development`
- **Depends on:** T3
- **Verification:** `python -m pytest tests/test_backtest_engine.py -q`
