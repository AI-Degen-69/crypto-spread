# Plan: Issue #138 — per-fill queue-position telemetry (tape-vs-tapeq EV)

Task Type: Code
Size Tier: Standard
Target Files: strategy/live_trader.py, scripts/bucket_fills.py (new), tests/test_fill_telemetry.py (new), tests/test_live_trader.py

## Task Breakdown

### Task 1: Rest context + queue-ahead math + book stash
- **Files**: `strategy/live_trader.py` (`MarketLiveState`, quote-placement block)
- **Type**: Code
- **Description**:
  1. Add per-leg rest-context fields (`rest_up_price/queue/ts/elapsed`, same for DOWN) + `last_bids_up/down` stash dicts to `MarketLiveState`; reset all on window rollover.
  2. Add `_queue_ahead(bids, price)` helper (sum sizes at levels ≥ price; empty → None), mirroring `run/sweeps/sim2.py:_queue_ahead`.
  3. At each placement tick: copy current bid books into the stash; when a leg newly rests, snapshot its rest context.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_fill_telemetry.py -q -k queue_ahead`

### Task 2: Telemetry record builder + best-effort writer + tape join
- **Files**: `strategy/live_trader.py` (new `_record_fill_telemetry` + `FILL_TELEMETRY_FILE = RUN_DIR / "live_fill_telemetry.jsonl"`)
- **Type**: Code
- **Description**:
  1. Builder computes `fill_ratio = printed / max(queue_ahead, 1)`, `ratio_flagged = ratio > 10`, all 15 fields (incl. `market_slug`/`condition_id` for the PnL join).
  2. Adopted improvement: also record `resting_pair_cost` (resting_up + resting_down at fill) enabling future pair-cost × queue analysis at zero re-collection cost.
  2. Tape join: timestamped variant of the data-api /trades fetch (same endpoint/schema as `markets.recent_trades`, keeping per-row ts); sum sizes at ≈ resting price with ts ≥ rest_ts; any failure → nulls.
  3. Writer appends one JSON line, wrapped so failure logs a warning and never raises; path injectable for tests.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_fill_telemetry.py -q -k "join or writer or ratio"`

### Task 3: Hook CLOB-confirmed + paper-simulated fill paths
- **Files**: `strategy/live_trader.py` (CLOB fill-confirmation block, paper-sim branches incl. chased immediate fill)
- **Type**: Code
- **Description**:
  1. Call the helper at every site that sets `filled_up/filled_down` (UP, DOWN, chased), passing `chased=` from `chased_leg` state.
  2. Exactly-once semantics per leg per window (guard against double-record on re-poll).
  3. Pure appendage: no change to fill conditions, prices, or order flow.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_live_trader.py tests/test_fill_telemetry.py -q`

### Task 4: Hook stream-detected fill path
- **Files**: `strategy/live_trader.py` (`on_user_order_event`)
- **Type**: Code
- **Description**:
  1. Call the same helper on stream UP/DOWN fills, using the stashed last-seen books for queue-ahead (null when stash empty).
  2. Same exactly-once guard as Task 3.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_fill_telemetry.py -q -k stream`

### Task 5: Bucketing helper script
- **Files**: `scripts/bucket_fills.py` (new)
- **Type**: Code
- **Description**:
  1. Read `run/live_fill_telemetry.jsonl`; bucket by `fill_ratio` ([0–0.25), [0.25–0.5), [0.5–1), [1+]); join settlement PnL via `market_slug` → `WINDOW_SETTLE` lines in `run/live_trades.jsonl` (missing → counted, PnL null).
  2. Print per-bucket table: count, mean subsequent PnL; exit 0 on empty input with a clear message.
- **Status**: [ ]
- **Verification**: `python scripts/bucket_fills.py run/live_fill_telemetry.jsonl` on synthetic fixture (covered by a test driving main() with tmp files)

### Task 6: Tests + regression gate
- **Files**: `tests/test_fill_telemetry.py` (new), `tests/test_live_trader.py`
- **Type**: Code
- **Description**:
  1. Cover: queue-ahead math incl. empty book; tape-join sum/window/filtering; one line per fill path (paper, CLOB-mocked with associate_trades, stream); degenerate nulls; writer failure tolerance (unwritable path); bucket table on fixture.
  2. Run targeted gate, then the full suite.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_live_trader.py -q` then `python -m pytest -q`
