# Plan — Issue #355, Sweep Visual Markets grid

> Supersedes the previous `#174` plan that lived in this file (that issue's Phase 1 work is
> already merged as `c9a431e`). Binding artifacts: `CONSTRAINTS.md`, `SPEC.md`.

## Classification

- **Size tier:** Standard — one file (`server/osc_dash.py`, backend + embedded frontend) plus
  one test file. Two architectural decisions (three-state grid, streaming progress cadence),
  no new dependency, no API/schema change.
- **Task type:** Code (primary) · Debug (the all-grey grid is the reported symptom) ·
  UX (the grid must stop lying about the selection).

## Grounding (verified this session, not assumed)

- `sweepCard()` `server/osc_dash.py:8001-8008` and `sweepCardTail()` `:8073-8080` both build
  `present` from `points[].series_present` only.
- The pre-fetch pending card (`:7764-7776`) and `renderSweepIdle()` (`:7839-7853`) pass
  `series_present: []` → all-grey by construction.
- `_run_sweep_worker()` `:2230-2257` reads+simulates all windows into `rows`; the
  `put_nowait` accumulation loop runs at `:2295-2313`, i.e. after the whole read.
- Measured against real `run/ticks/`: `n_windows=105`, all ten slugs in
  `points[0]["series_present"]`, first progress event carried only `['btc-up-or-down-5m']`.
- CSS block `:4929-4932`; chips `:4700-4721`; handlers `toggleBtToken` `:6824`,
  `setBtTokensAll` `:6837`, `setBtDuration` `:6842`, `updateBtFilterUI` `:6804`;
  `btControlQuery()` `:6946-6971`; `renderSweepVisual()` `:8326-8408`.

## Risk-first ordering

The riskiest item is the backend change: it touches the progress contract that
`test_sweep_stream_progress_before_final_and_parity` and
`test_sweep_worker_progress_points_converge` depend on, and getting the cadence wrong turns
a 6.8 GB dataset into tens of thousands of SSE events. It runs **first**, while reverting is
one commit. The frontend grid is additive and low-risk.

## Tasks

| ID | Size | Tag | Files | What | Depends on | Verification |
|---|---|---|---|---|---|---|
| [x] T1 | M | Code/Debug | `server/osc_dash.py` | Add `SWEEP_PROGRESS_MIN_INTERVAL_SEC`; extract a local accumulation step in `_run_sweep_worker` reused by live + post-loop passes; emit the first progress event from inside the streaming read loop (`rows_total: None`, running `n_snaps`, non-empty `series_present`), then at most once per interval; replace per-row post-loop emission with one converged snapshot (`rows_done == rows_total`, `points` equal to final). Keep `emit_progress`, `put_nowait` failure handling, the `offset` queue-memo skip, `_select_sweep_bests()` and the returned dict. | — | `python -m pytest tests/test_osc_dash_integration.py -q -k sweep` |
| [x] T2 | S | Code | `server/osc_dash.py` | Add `btSelection()` + `btSelectedSeriesSlugs()`; make `btControlQuery()` consume the helper with byte-identical output. | — | `python -m pytest tests/test_osc_dash_integration.py -q -k "control or token or duration"` |
| [x] T3 | M | Code/UX | `server/osc_dash.py` | Add `sweepMarketsGridHtml(points, selectedSlugs)` with three states (`solid` / `pending` / `off`) + per-chip `title`; replace the duplicated grids in `sweepCard()` and `sweepCardTail()`. Empty/absent `selectedSlugs` preserves today's two-state contract. | [x] T2 | frontend contract test |
| [x] T4 | S | Code/UX | `server/osc_dash.py` | Add `.sweep-mkt.pending` CSS (theme variables only). Wire `selected_series` snapshot at run start through the pending card, `buildSweepProgressView()` (accept `rows_total: null` → "N windows replayed…") and `renderSweepVisual()`; `renderSweepIdle()` reads live selection; chip handlers re-render the idle card only when no sweep is active and the card is idle. | [x] T3 | frontend contract test + `tests/test_theme_tokens.py` |
| [x] T5 | M | Test | `tests/test_osc_dash_integration.py` | Extend `test_sweep_worker_progress_points_converge` (first msg `rows_total is None` + non-empty `series_present`; last msg `rows_done == rows_total` and `overall`/`per_series` equal the result), add the monkeypatched-interval bounded-count test, extend the grid tests for all three states, snapshot flow and null totals. Preserve the existing fallback assertions. | T1, T3, T4 | `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` |

**Checkpoints**
- After T1: backend progress is streaming and bounded; `final` parity intact.
- After T3/T4: the grid is selection-driven with three states.
- After T5: full targeted gate green.

## Rejected proposals (recorded so they do not resurface)

- **Change which markets the sweep replays** — rejected: selection semantics are correct and
  covered by `test_sweep_honours_market_and_duration_selection`; this is a display bug.
- **Add a `phase` field to the progress message** — rejected: `rows_total: null` already
  expresses "total not yet known" without widening the message contract.
- **Emit every N windows** — rejected: window counts are dataset-dependent and unknown
  mid-stream; a time interval bounds the event count regardless of corpus size.

