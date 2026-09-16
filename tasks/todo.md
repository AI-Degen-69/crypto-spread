# tasks/todo.md — Issue #201

- [ ] T1 [Design/UI] Restructure stop-loss markup: toggle header + `btStopLossFields` wrapper, delete the old select (`server/osc_dash.py`)
- [ ] T2 [Design/UI] `toggleStopLossInputs()` + `.checked ? '1' : '0'` in `runBacktest()` + `resetBtParams()` restore (`server/osc_dash.py`)
- [ ] T3 [Test] HTML-string assertions for wrapper, checkbox markup, handler wiring, request value (`tests/test_osc_dash_integration.py`)
- [ ] Gate: `python -m pytest tests/test_osc_dash_integration.py -q` then `python -m pytest -q`
