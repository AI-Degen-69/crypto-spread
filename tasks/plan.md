# Plan — Issue #377: Backtest window detail repeats fill prices, empty Time/Duration

Branch: i377/backtest-window-detail-repeats-fill-prices-and-lea | Issue: #377
Stack: Python 3.12 · FastAPI dashboard (`server/osc_dash.py`, JS renderer embedded) · pure backtest engine (`backtest/engine.py`) · pytest
Size: **Small** — 2 source files + tests; the end-to-end pipe (`completed_pairs` → `pairs` → `trades_sample` → `pairRec` reader) already exists, work is extending record shape + rendering.
Task type: **Code, Debug** (user-facing correctness/telemetry bug, no new feature)

## Issue in one line
The expanded window-detail trade table shows copy-paste fill prices, a timestamp
only on the first row, and `—` in every Duration cell, so per-trade timing cannot
be verified. Scope is telemetry-only; #376 owns the cost arithmetic.

## Embedded spec (Small — no SPEC.md ceremony)
- Goal: every pair row shows its own UP/DOWN fill prices (already works via
  `pairRec`), its own leg-fill timestamps, and a real Duration; every
  STOP/EXIT row shows entry-fill time + entry-to-exit duration.
- Acceptance: the issue's 4 checkboxes (per-pair prices/times/durations, stop
  times/durations, `trades_sample` plumbing + two-pair regression test, both
  suites green).
- Edge cases: single-pair windows render unchanged except real values; legacy
  payloads without timing keys fall back to today's rendering; unresolved
  settlement shows `—` for exit time; dead-zone close vs adverse-drift exits
  keep their distinct pill labels; stop-then-pair chronology keeps one running
  `tradeIdx`.
- Out of scope: #376 arithmetic, any engine decision-logic change, live trader,
  sim2, sweep visuals (#410).

## CodeRabbit intake (read once; echo ignored)
- Adopted: 3-phase skeleton (engine ledger → serializer → renderer + tests);
  per-leg fill times; executed-exit time for stops (engine only books an exit
  when an executable bid exists, so the booked tick IS the exit); absolute tick
  `ts` as wall-clock source; economics-unchanged regression.
- Rejected: a new `trade_log` field on `WindowResult` — the pipe already exists
  (`engine.py:1327` record → `engine.py:1523` `pairs=` → `osc_dash.py:2283`
  `"pairs": w.pairs` → renderer `pairRec` at `osc_dash.py:9426`). Extending the
  existing record is ~30 lines instead of a new field + two serializers.
- Recorded deviation: pair Duration = **first-fill-to-merge** (naked-leg
  exposure), NOT the ticket default second-fill-to-merge — the engine merges on
  the second-fill tick so the default is always 0s. Matches the existing
  tooltip ("Time from first fill to merge completion", `osc_dash.py:9457`) and
  the column header ("Time in market with leg exposure before resolution").
- Verified seams (spot-checked, `[UNVERIFIED]` count: 0): `WindowResult` +
  `pairs` field (`engine.py:647/700`), locals (`engine.py:972-973`), fill latch
  (`engine.py:1295-1303`), merge branch (`engine.py:1305-1350`), naked clock
  (`engine.py:1355-1359`), dead-zone close (`engine.py:1366-1396`), exits
  (`engine.py:1404/1428`), settlement (`engine.py:1470-1486`), construction
  (`engine.py:1491-1524`), `trades_sample` (`osc_dash.py:2254-2283`), merge-row
  loop + placeholders (`osc_dash.py:9420-9467`), stop rows
  (`osc_dash.py:9469-9505`). Issue line numbers drifted (file grew); structures
  confirmed. No missing-persona skip: `code-explorer` not needed (2-file scope,
  seams verified by direct read).

## Resolved open questions (from code, not asked)
- Q1 (per-leg timestamps + duration definition): yes — UP row shows UP-leg fill
  time, DOWN row shows DOWN-leg fill time; Duration = first-fill-to-merge (see
  deviation above). No new clock needed: `cur_ts`/`elapsed` are in scope at
  every site.
- Q2 (stop rows entry time + entry-to-exit): yes — new `stops` records carry
  entry + exit timing; renderer iterates them.

## Improvement proposal (adopted by default — simplification)
Extend `completed_pairs` instead of adding `trade_log`. Evidence, verbatim:
`completed_pairs.append({ "entry_up": resting_up, ... })` (`engine.py:1327`),
`pairs=completed_pairs` (`engine.py:1523`), `"pairs": w.pairs`
(`osc_dash.py:2283`), `const pairRec = (t.pairs && t.pairs[m] != null) ?
t.pairs[m] : null` (`osc_dash.py:9426`). No evidence for a second parallel list;
a new field would duplicate the existing per-pair pipe. Adopted.

## Interfaces (locked before build)
`completed_pairs` record (additive — existing 4 keys untouched):
`{entry_up, entry_down, pair_cost, edge_cents, fill_ts_up, fill_elapsed_up,
fill_ts_down, fill_elapsed_down, resolve_ts, resolve_elapsed, duration_sec}`
New `WindowResult.stops: list[dict] = field(default_factory=list)` (direct
constructors keep working; `SURFACE_KEYS` untouched):
`{kind: dead_zone_close|stop|settle, side, entry_price, entry_ts,
entry_elapsed, exit_price, exit_ts, exit_elapsed, duration_sec, fees_cents,
pnl_cents}` — deltas captured from the expressions each branch already
evaluates, never recomputed.
`trades_sample` row gains `"stops": w.stops` (`pairs` already passed through).
Renderer: merge rows read `pairRec.fill_*`/`duration_sec` with fallback to
`timeStr1`/`—`; stop rows iterate `t.stops` with fallback to the legacy
single-exit rendering when absent.

## Dependency graph & tasks
T1 (engine records) → T2 (serializer) → T3 (renderer). Risk-first: T1 carries
the only shape decision, so it runs first.

- [x] T1 [Backend/Logic] M — `backtest/engine.py`: latch per-leg
  `fill_ts/elapsed` beside the entry-price latch; extend the merge record;
  append `stops` records at dead-zone close + both adverse exits + terminal
  settlement; add `WindowResult.stops` defaulted field. Verify: new unit tests
  (two-pair window timing, stop timing, economics-unchanged) + `pytest
  tests/test_backtest_engine.py -q`. Depends on: none.
- [x] T2 [Backend/Logic] S — `server/osc_dash.py` serializer: pass `stops`
  through `trades_sample`. Verify: integration assertion on row shape +
  targeted `pytest tests/test_osc_dash_integration.py -q -k "trades_sample"`.
  Depends on: T1.
- [x] T3 [Design/UI] M — `server/osc_dash.py` renderer: per-leg Time +
  Duration on merge rows; `t.stops` iteration on stop rows; legacy fallback.
  Verify: embedded renderer tests + live browser check of one expanded window
  (Time/Duration cells populated). Depends on: T2.

Checkpoint after T1: engine records + unit tests green (one-line progress note,
not an approval pause — Mode A).

## Files NOT to modify
`tests/test_engine_parity.py`, `strategy/live_trader.py`, `research/sweeps/*`,
`backtest/index.py`, `docs/*`, `SPEC-319.md`.
