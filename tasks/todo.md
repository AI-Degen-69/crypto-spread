# Tasks — Issue #155

- [x] **T1 [Design/UI]: Header + SERIES cell** — 7-column header, hyperlink SERIES cell with HH:MM-HH:MM range, drop WINDOW/LINK (`server/osc_dash.py`)
- [x] **T2 [Design/UI]: Entry-relative MAX UP/DOWN** — `max_mid - start_mid` / `start_mid - min_mid` + ≥$0.05 highlight, null-safe (`server/osc_dash.py`)
- [x] **T3 [Design/UI]: RESULT pill** — green UP / red DOWN / `-` via `pill()` helper (`server/osc_dash.py`)
- [x] **T4 [Code]: Regression tests + full-suite gate** — new `tests/test_recent_windows_table.py`, targeted gate + `test_osc_dash_integration.py` + full `pytest -q`
