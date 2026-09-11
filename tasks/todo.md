# Todo: Issue #139 — cockpit queue panel + PnL histogram

- [x] Task 1: Queue-telemetry endpoint — aggregation, verdict, empty state (`server/osc_dash.py`)
- [x] Task 2: Queue panel UI — bars, verdict, empty state, fallback (`server/osc_dash.py`)
- [x] Task 3: PnL histogram UI — binning, zero bin, mean + CI-lo, fallback (`server/osc_dash.py`)
- [ ] Task 4: Tests + regression gate (`python -m pytest tests/test_osc_dash_integration.py -q`, then `python -m pytest -q`)
