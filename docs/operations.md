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
| `server/osc_dash.py` | FastAPI dashboard (`:8802`) — sole canonical dashboard for backtest analytics, replay, and live trader cockpit. |

### Dashboard Runtime & Stack

`server/osc_dash.py` (FastAPI on `:8802`) is the sole canonical dashboard for this repository.

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
http://127.0.0.1:8802/api/backtest?offset=0.02&queue=50&pair_cost=0.99&quote_lo=0.10&quote_hi=0.90
http://127.0.0.1:8802/api/ticks/manifest
```

CLI flags map 1:1 to `BacktestParams` fields — `--offset`, `--queue`,
`--pair-cost` (the `max_pair_cost` chase ceiling, 0.50-1.00),
`--quote-lo` and `--quote-hi` (bounds on quotable two-sided mid, 0.00-1.00),
`--exit <slug>=<thresh>` (repeatable), `--exit-default-5m`,
`--exit-default-15m`, `--size`, `--gas`.

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
