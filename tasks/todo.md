# TODO — Issue #355

- [x] T1 [M] Backend: streaming, rate-limited sweep progress (`server/osc_dash.py`)
- [x] T2 [S] Frontend: one selection source (`btSelection`, `btSelectedSeriesSlugs`)
- [x] T3 [M] Frontend: three-state Markets grid builder + replace both duplicated grids
- [x] T4 [S] Frontend: `pending` CSS + selection snapshot through all render paths
- [x] T5 [M] Tests: convergence, bounded count, three states, snapshot flow, null totals

All gates green: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
=> 275 passed. Verified live in Chrome (real page, real CSS): chips=10 pending=10 off=0 solid=0
with all five markets selected; SSE first progress event at 0.01s with rows_total=null.
