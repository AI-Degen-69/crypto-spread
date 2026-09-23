# Tick Capture + Backtest — Operations (Replay & Simulation)

> Companion to `docs/research-spread-bot-conclusions.md` (data model, measured
> findings). This file is the **operator runbook** for the replay-grade
> capture + offline sweep system added on top of the original `osc_dash`. Live trading
> execution is managed by `strategy/live_trader.py` and the canonical dashboard.

## What it is

| Component | Purpose |
|---|---|
| `scripts/collect_ticks.py` | Forks `measure_5m_oscillation.py` and writes **full** UP+DOWN book depth + tape delta to `run/ticks/ticks_YYYY-MM-DD.jsonl`. Cadence is round + `POLL_INTERVAL`, ~1.4s today — read `sampling_interval_s` from the manifest, not the 1s the old docs claimed (#167) |
| `scripts/shadow_ev_pilot.py` + `scripts/run_layout.py` | Paper EV pilot writing self-contained `runs/{paper,live}/YYYY-MM-DD_HH-MM_TZ/` (`data/`, `research-papers/`, `summary.html`, `manifest.json`); convention: `docs/run-conventions.md` |
| `backtest/engine.py` | Pure function `replay(snaps, params) -> results`. Consumes tick jsonl, simulates SPREAD-2 (resting bid at `mid-offset`, queue gate, monotonic exit, pair capture). |
| `backtest/index.py` | Per-file `<file>.jsonl.idx` sidecar (cid -> byte offset, ts). First backtest on a file scans once; subsequent calls jump to cid spans. |
| `scripts/backtest.py` | Thin CLI: `python -m scripts.backtest run/ticks/ --offset 0.02 --queue 50 --exit btc-up-or-down-5m=0.09` |
| `server/osc_dash.py` | FastAPI dashboard (`:5515`) — sole canonical dashboard for backtest analytics, replay, and live trader cockpit. |

### Dashboard Runtime & Stack

`server/osc_dash.py` (FastAPI on `:5515`) is the sole canonical dashboard for this repository.

## Run a day of capture

```powershell
cd "C:\Users\Tiger\Agents\Projects\AI Trading\crypto-spread"
pip install -r requirements.txt
python -m scripts.collect_ticks                 # continuous
python -m scripts.collect_ticks --once          # one poll (smoke)
python -m scripts.collect_ticks --days 1        # stop at next UTC midnight
python -m scripts.collect_ticks --out E:\ticks  # custom output
python -m scripts.collect_ticks --gzip          # .jsonl.gz rotation
```

Output:
```
run/ticks/ticks_2026-08-29.jsonl   # ~150MB raw, ~20MB gz
run/ticks/manifest.json            # line count, series seen, last update ts,
                                   # tape/socket health, and cadence:
                                   #   sampling_interval_s  real gap between snaps
                                   #   tick_ms_last/_max    round duration
                                   #   tick_ms_first        cold opening round
```

Per-series failure is isolated: a 429 on one CLOB call only skips that series
for that tick (`err` field on the snap). A slow tick (>`TICK_BUDGET_MS`,
currently 1500 ms) is logged but does not crash the loop. The opening round is
several times slower than a warm one (cold gamma cache, cold TLS pool, socket
still connecting); it is reported as `tick_ms_first` and judged against the
looser `COLD_TICK_BUDGET_MS` (15000 ms, logged as `slow_first_tick`), so
`--once` reads `errs=0` on a healthy connection but a wedged cold start is
still reported.

## Run a sweep

CLI:
```powershell
python -m scripts.backtest run/ticks
python -m scripts.backtest run/ticks --offset 0.02 --queue 50 --exit btc-up-or-down-5m=0.09
python -m scripts.backtest run/ticks/ticks_2026-08-29.jsonl --out run\backtest\baseline.json
```

Dash:
```
http://127.0.0.1:5515/api/backtest?offset=0.02&queue=50&pair_cost=0.99&quote_lo=0.10&quote_hi=0.90
http://127.0.0.1:5515/api/ticks/manifest
```

CLI flags map 1:1 to `BacktestParams` fields — `--offset`, `--queue`,
`--pair-cost` (the `max_pair_cost` chase ceiling, 0.50-1.00),
`--quote-lo` and `--quote-hi` (bounds on quotable two-sided mid, 0.00-1.00),
`--dead-zone-val` and `--dead-zone-unit` (the tail of the window that is
untradeable — `pct` fraction 0.00-1.00 or absolute `sec`),
`--naked-leg-at-expiry close|hold` (what an unpaired leg does in the dead
zone), `--exit <slug>=<thresh>` (repeatable), `--exit-default-5m`,
`--exit-default-15m`, `--size`, `--gas`. `--max-start-delay` and
`--filter-partial` filter the *dataset* before replay; they are not engine
parameters (issue #229 deleted `max_start_delay_sec`).

**There is no fill model to choose.** One rule, hard-coded, the same one the
live engine runs: a resting buy fills when a trade prints at our price *or*
the best ask passes fully through it, and it fills at our own price with no
fee because a limit order that waits is a maker order. See
`docs/engine-decision-rules.md` §3 and ADR-0002.

## What the result means

```
Overall (640 windows):
  pair_rate    0.0%   exit_rate   0.0%
  total_pnl     +0.00c   avg_pnl   +0.00c/win

Per series:
  btc-up-or-down-5m   n=98  pair=0.0%  exit=0.0%  pnl=+0.00c  osc=72 mono=26
```

- **pair_rate**: fraction of windows where both sides filled and merged for
  4¢ gross (minus a share of merge gas).
- **exit_rate**: fraction of windows where the simulator's exit rule fired
  (`exit_taken` in `backtest/engine.py`) — one side filled and mid drifted past
  threshold without reversal. Unresolved one-sided positions that never hit the
  exit threshold are *not* counted as exits.
- **osc/mono**: count of oscillating vs monotonic windows, classified by
  `classify_window` (max excursion in each direction vs 0.50).

For 5m BTC/SOL/ETH/BNB/XRP, the measured universe is ~73% oscillating and
~27% monotonic (`run/oscillation_windows.jsonl`). If `pair_rate` is much
below oscillating rate, the queue gate or the offset is suppressing fills
that the data says are reachable. Pair cost is not a candidate: issue #227
deleted the entry-side pair-cost block, and `max_pair_cost` now bounds the
leg chase only.

## Sample Discrepancies & Replay Integrity

The **Sample Discrepancies** panel on the dashboard's Tick Files tab (and `--verbose` output in `scripts/verify_tick_data.py`) surfaces line-level issues discovered during dataset validation.

### Discrepancy Categories & Verification Checks

The tick verifier (`scripts/verify_tick_data.py`) tracks data quality across sampled discrepancies and separate file-level counters:

1. **Sampled Discrepancies (`sample_issues`)**:
   - **JSON Decode Errors (`json_decode_error`)**: The raw line is unparseable as JSON (e.g. truncated line, write collision, non-JSON bytes). Tracked in the `corrupt_lines` counter. These rows cannot be parsed into a dictionary and convey no market state.
   - **Schema & Book Issues (`schema_or_book_issue`)**: The line parses as JSON, but violates structural or value invariants verified by `verify_tick_record`:
     - Missing or null required fields (`REQUIRED_FIELDS`: `ts`, `cid`, `series`, `duration`, `start_ts`, `end_ts`, `up_book`, `down_book`).
     - Invalid timestamps (`ts <= 0`, non-numeric, or `start_ts > end_ts`).
     - Value bounds violations (`mid` outside `[-0.01, 1.01]`, or `touch_pair` outside `[0.50, 1.50]`).
     - Structural book anomalies (non-dict book, unparseable level entries) or **crossed books** (`best_bid >= best_ask`).
     - Malformed tape deltas (non-list `tape_delta` or malformed trade objects).
   - Tracked in `schema_errors`, with `crossed_books` and `book_anomalies` counted separately.

2. **Collector Errors (`err` field)**:
   - Snapshots where the collector failed to query a leg from the venue (e.g. HTTP 429 rate limit or read timeout) and recorded `{"err": ...}`.
   - Tracked in the separate `collector_errors` scalar counter. Note that `collector_errors` is reported as its own counter and is not appended to `sample_issues` (an errored record that also fails schema or book checks will generate a separate entry in `sample_issues`).

### Sample Capping vs Full-Population Counters

- **Capped Debug Samples:** To protect memory and network payload sizes on multi-gigabyte tick files, `verify_tick_file` caps `sample_issues` at **20 entries per file** (`max_sample_issues=20`).
- **Dashboard Slicing:** The dashboard's Tick Files tab displays the first **10 entries** of `sample_issues` (`server/osc_dash.py:4994`).
- **Full-Population Totals:** The scalar metrics (`corrupt_lines`, `schema_errors`, `crossed_books`, `book_anomalies`, `collector_errors`) reflect the **entire file population**. A short list in the Sample Discrepancies box does **not** mean the file has few errors; always inspect the aggregated totals above the sample box.

### The Backtest Engine's Silent-Skip Contract

During replay, the backtest engine intentionally tolerates malformed rows:
- `backtest/engine.py:74` (`_json_or_skip`) returns `None` for empty lines, non-dict payloads, or JSON parse errors.
- `backtest/engine.py:87` (`iter_ticks`) silently discards `None` items.
- Replay executes without raising exceptions or logging skipped lines.

Because replay never warns about dropped rows, a backtest on corrupted data will still exit cleanly with "success". **Running the verifier (`python -m scripts.verify_tick_data run/ticks`) or reading the Tick Files tab is a required pre-flight data-quality gate**, not an optional diagnostic.

### Operational Guidance: When to Care

Not every reported discrepancy invalidates a backtest run:

| Severity | Discrepancy Type | Impact on Replay | Operational Action |
|---|---|---|---|
| **Tolerable Noise** | Isolated late start (<5s) or early cutoff (<5s) | Negligible; window simply lacks warmup or cooldown tail ticks | Acceptable if valid ticks cover the core trading duration |
| **Tolerable Noise** | Occasional collector `err` (<0.1% of ticks) | Brief 1-tick hiatus in state; trade tape catches up next round | Safe to replay; check that total window count matches expected |
| **Tolerable Noise** | Rare corrupt line (`corrupt_lines` < 0.01% of total) | Silent drop of a single tick | Safe if isolated and not at quote placement or fill moments |
| **Replay Gate (Warning)** | Frequent sampling gaps (`delta > max_gap_sec`, default >6s, `--max-gap`) | Trajectory discontinuity; fills or price excursions may be missed | Review capture health; results carry lower temporal confidence |
| **Replay Invalidation (Hard Gate)** | **Crossed books (`crossed_books > 0`)** | Inverts the spread (`best_bid >= best_ask`); distorts resting maker fill logic | **Do not trust fills in windows with crossed books** |
| **Replay Invalidation (Hard Gate)** | Out-of-bounds `mid` or missing book legs | Distorts quote placement and offset calculation | Quarantine or re-capture the file |

### Related Reading Hazard: `mid` vs `the recorded mid`

As defined in [`docs/glossary.md`](glossary.md):
- **`mid`** means the true two-sided mid (`book_math.two_sided_mid`), requiring valid books on both legs. If either leg cannot be priced, it is `None`.
- **`the recorded mid`** is the `"mid"` field in tick JSONL files (`book_math.mid(up_book)`), reflecting the **up leg alone**.
This distinction is an intentional data-model rule and must not be conflated with a schema error or sample discrepancy.

## Tuning hints

1. **Set `queue_gate=0` to see what fills look like with no queue
   constraint.** If pair_rate is still < oscillating rate, the issue is the
   offset, not queue and not pair cost.
2. **Compare `pair_rate` against the oscillating rate** to see how much
   the gates are costing. The fill rule itself has no looser setting to
   fall back on.
3. **Run with `--offset 0.01` and `--offset 0.03`** bracketing 0.02. If
   0.01 is not much better than 0.02, the queue is the binding constraint,
   not the price level.
4. **Per-asset exit threshold tuning:** `--exit btc-up-or-down-5m=0.08`
   cuts BTC earlier; the dashboard's defaults (BTC 9¢, SOL 11¢, others
   12¢, 15m 13¢) come from the monotonic-rate table in
   `server/osc_dash.py:357-361` — not enforced, only suggested.

## Tests

```powershell
python -m pytest tests/ -v
```

49 tests covering: classification thresholds, the fill rule, queue
gating (including 0=disabled), monotonic exit (with and without reversal),
replay determinism (same input + params = identical pnl hash), gzip
roundtrip, per-file cid sidecars (5 tests), CLI smoke (4 tests).
Total: 44 (engine) + 5 (index) + 4 (smoke) = 49.

## Scope of Replay & Backtesting Subsystem
 
- **Backtesting is pure capture + simulation.** The replay engine performs
  pure offline replay without live venue calls. Live order execution is handled
  independently by `strategy/live_trader.py`.
- **Mixed transport: streamed tape, polled books.** Since #165 the trade tape
  comes from the CLOB market websocket (`strategy/streaming.py`), which is what
  cut snapshot tape starvation from ~98.6% to ~58%. Order books are still
  fetched over REST every round, so book freshness is bounded by the round, not
  by the venue. Since #167 the effective cadence is round + `POLL_INTERVAL`
  (~1.45s, published live as `sampling_interval_s`) rather than the 1s this
  section used to claim: `requote_interval` in `strategy/config.py:637` and the
  documented `post_venue_accept_ms=81` should be read against that real figure.
- **No V2 pUSD migration.** `merge_gas_usd 0.05` is a placeholder; verify
  against the real on-chain figure before any live merge.

## File map

```
strategy/series.py          # 10-series universe (single source of truth)
scripts/collect_ticks.py    # full-depth + tape capture (this lab)
scripts/backtest.py         # CLI wrapper
scripts/measure_5m_oscillation.py   # original top-of-book collector (unchanged)
backtest/
  __init__.py               # public API
  engine.py                 # pure replay() + BacktestParams
  index.py                  # per-file cid offset cache
server/osc_dash.py          # dashboard (extended with /api/backtest, /api/ticks/manifest)
tests/
  test_backtest_engine.py   # 44 tests
  test_backtest_index.py    # 5 tests
  test_collect_ticks_smoke.py # 4 tests
run/ticks/                  # gitignored output
```
