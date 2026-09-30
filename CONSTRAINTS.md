# CONSTRAINTS.md — Issue #365 Guardrails

## Quality & Non-Regression Rules
1. **Zero Strategy Drift:** Presentation & UI only. No modifications to `backtest/engine.py` strategy logic, trading rules, or metrics calculation.
2. **Zero "For Profit" Claims:** No header, tooltip, badge, or pill in the backtest UI may claim that a pair capture implies profit.
3. **Card & Streaming Parity:** The provisional live sweep render (`btAppendProvisionalPoints`) and final result render (`renderBacktestResult`) must produce identical KPI card labels, values, and sub-labels for the same payload.
4. **Master-Detail Reconcilability:** The Executed Windows Log table must provide an expandable master-detail structure where the master row summarizes the window (Market, Total P&L $ and %, Merges, Stops, Dead Zone, Resolution) and the expanded child view cleanly presents tabular trade events (Side, Time with Elapsed, Fill Price, Pair Cost, Duration, Status, Trade P&L, hover tooltips).
5. **Clean Tabular Data:** Child tables must be in English with zero clutter/prose; all auxiliary explanations must reside strictly in hover tooltips.
6. **Targeted Test Gate:** Fast targeted integration tests (`python -m pytest tests/test_osc_dash_integration.py -q`) must pass.
