# SPEC.md — Issue #138: per-fill queue-position telemetry (tape-vs-tapeq EV)

## 1. Goal
Record, for every live entry fill, how deep in the queue the fill happened (queue ahead at rest + printed trade size that consumed it), persisted to a JSONL sidecar, plus a bucketing helper that renders the live tape-vs-tapeq verdict. Pure observation: zero change to quoting/fill logic.

## 2. In Scope
1. **Rest context (`strategy/live_trader.py`)**: when an entry order rests, store per leg `queue_ahead_at_rest` = sum of bid size at prices ≥ our resting price on that leg's book (mirrors sim2 `_queue_ahead`), plus resting price, rest timestamp, and window elapsed. Books arrive full-depth in `poll_data` (`full_book` dicts); tests synthesize them.
2. **Fill hook**: single `_record_fill_telemetry(mstate, leg, ...)` helper called from all fill sites — CLOB-confirmed (UP/DOWN), paper-simulated (UP/DOWN + chased immediate), and stream-detected (`on_user_order_event` UP/DOWN). Stream path has no book context: fall back to the last stashed per-tick bid books (`last_bids_up/down`, copied each tick; small).
3. **Tape join**: at fill time, best-effort fetch of timestamped prints for the condition and sum of sizes at ≈ resting price with ts ≥ rest_ts → `printed_size_at_price_since_rest`. `recent_trades()` aggregates without timestamps, so the join uses a small timestamped variant (same endpoint/schema). Any failure → `null` fields, fill path never blocked.
4. **Sidecar writer**: append one JSON line per fill to `run/live_fill_telemetry.jsonl` (gitignored, same family as `TRADES_FILE`). Fields: ts, slug, market_slug, condition_id, leg, chased, resting_price, fill_price, queue_ahead_at_rest, printed_size_at_price_since_rest, filled_size, fill_ratio, ratio_flagged (>10), window_elapsed_sec, mid_at_fill, resting_pair_cost. Best-effort: write failure logs a warning and never blocks trading. (`market_slug`/`condition_id` are a documented superset of the issue list — required to join subsequent-window PnL; `resting_pair_cost` is the adopted improvement.)
5. **Bucketing helper** (`scripts/bucket_fills.py`): reads the JSONL, buckets by `fill_ratio` ([0–0.25), [0.25–0.5), [0.5–1), [1+]), joins each fill's window settlement PnL via `market_slug` → `WINDOW_SETTLE` trades, prints count + mean subsequent PnL per bucket.
6. **Tests**: queue-ahead from synthetic book, tape-join math, one telemetry line per fill path (paper, CLOB-mocked, stream), degenerate nulls, writer failure tolerance.

## 3. Out of Scope
- Any change to quoting/fill/exit logic (observation only).
- Dashboard visualization (sibling issue #139).
- Chased-fill queue depth beyond the `chased` flag (chased fills legitimately run ~0 queue).

## 4. Acceptance Criteria
- [ ] Every entry fill (CLOB, stream, chased, passive) appends exactly one line with all fields; `fill_ratio` flagged when > ~10.
- [ ] Empty book / missing tape → `null` fields, no crash, fill proceeds.
- [ ] Write failures never block trading; file lives under `run/`.
- [ ] Helper prints per-bucket (count, mean subsequent PnL) table.
- [ ] New unit tests cover queue-ahead math, tape join, and all fill paths.
- [ ] `python -m pytest tests/test_live_trader.py -q` passes, plus the new tests.
