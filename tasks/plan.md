# Task Plan — Issue #266: Sweep Visual sensitivity clarity

**Size tier:** Standard — one dashboard endpoint and one served Chart.js component need a response-contract extension, visualization change, and focused regression coverage.
**Task type:** Code + Design/UI + API/Backend + QA/Regression.
**Related issue:** #264 delivered the initial Sweep Visual.

## Issue-driven decisions
- Use discrete bar charts because every tested X value is an independent replay; no line interpolation is implied.
- Replace `exit_5m` with `exit_stop`, a shared stop-distance sweep that sets the 5m and 15m default stop values together.
- Define the best market as the canonical market with the highest per-market P&L across all tested sweep points; ties resolve by canonical order. Show its friendly label, tested value, and P&L.
- Humanize the axis selector while preserving machine axis names in the API.

## Tasks

- [x] **TASK-1 [API/Backend]**: Align the sweep axis contract and shared stop semantics.
  - Target: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`.
  - Build: replace `exit_5m` with `exit_stop`; accept both 5m and 15m base stop values; apply every `exit_stop` value to both default duration stop entries in the copied sweep parameters; preserve validation and safe file handling.
  - Verify: endpoint tests prove both duration values move together and invalid-axis responses list the new four axes.

- [x] **TASK-2 [API/Backend]**: Add deterministic best-result metadata.
  - Target: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`.
  - Build: return additive `best_overall` and `best_market` metadata; handle empty points, absent markets, negative values, and deterministic ties without changing replay math.
  - Verify: response tests cover populated, empty, sparse, negative, and tied results.

- [x] **TASK-3 [Design/UI]**: Humanize the parameter selector and explain independent runs.
  - Target: `server/osc_dash.py`, `tests/test_theme_tokens.py`.
  - Build: replace technical option text with operator-facing descriptions for queue depth, quote offset, shared 5m+15m stop distance, and reversal buffer; update helper text to say each bar is an independent replay.
  - Verify: served-HTML tests assert the four humanized labels, absence of `exit_5m` in the Sweep Visual menu, and stable DOM IDs.

- [x] **TASK-4 [Design/UI]**: Convert all Sweep Visual charts from lines to discrete bars.
  - Target: `server/osc_dash.py`, `tests/test_theme_tokens.py`.
  - Build: render the aggregate and ten market charts as bar datasets with a zero baseline, signed values, readable tooltips, and no interpolating line configuration.
  - Verify: rendering-contract tests assert bar chart configuration, zero-baseline behavior, numeric values, one aggregate canvas, and ten market cards.

- [x] **TASK-5 [Design/UI]**: Mark the best aggregate point and best market.
  - Target: `server/osc_dash.py`, `tests/test_theme_tokens.py`.
  - Build: visibly emphasize the best aggregate bar; highlight the best market card/bar and display its friendly label, tested parameter value, and P&L; render neutral metadata for empty responses.
  - Verify: HTML/JS contract assertions cover both markers and the empty state; browser check confirms the markers are visible.

- [x] **TASK-6 [QA/Regression]**: Verify the real dataset and update handoff evidence.
  - Target: `tasks/plan.md`, `tasks/todo.md`; no generated data.
  - Build: run the browser against `run/ticks/ticks_2026-09-18.jsonl`, record chart count, shared-stop axis text, best markers, and any zero-fill caveat.
  - Verify: targeted pytest gate plus browser snapshot and console/network inspection. Completed with 152 focused tests, a queue sweep against `ticks_2026-09-18.jsonl` (30 windows), one aggregate bar chart, ten market bars, and visible best-result metadata; browser console had no errors.

## Verification matrix
| Task | Verification |
|---|---|
| TASK-1 | API contract and shared 5m/15m stop tests |
| TASK-2 | Best metadata edge-case tests |
| TASK-3 | Served HTML label contract tests |
| TASK-4 | Chart configuration and DOM contract tests |
| TASK-5 | Marker metadata tests + browser visibility |
| TASK-6 | `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` + browser run |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| Bars hide trend shape | Keep tested values ordered and show exact parameter labels/tooltips; do not interpolate. |
| 5m/15m semantics remain ambiguous | Use one `exit_stop` machine axis and one humanized “5m + 15m markets” label; test both defaults per point. |
| Missing markets appear artificially best | Zero-fill missing markets and exclude missing-data defaults from best selection. |
| Negative P&L is hard to read | Enforce a visible zero baseline and signed bar colors. |
| Old clients send `exit_5m` | Return the documented invalid-axis response; no silent alias that hides the contract change. |

## Out of scope
Strategy/backtest calculations, production parameter defaults, tick data, other tabs, structural-limit sweeps, joint/random sweeps, new dependencies, and Issue #174 work.
