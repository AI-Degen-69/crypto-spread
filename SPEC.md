# SPEC — Issue #214: Engine Parity Test Harness

Binding while `feat/parity-harness-214` is live. Per-issue working file (`docs/git-workflow.md` §5)
— not an architecture document.

## 1. Goal

Provide an automated, executable, high-fidelity parity test harness that enforces behavioral identity
between the live execution path (`strategy/live_trader.py:LiveTraderEngine` in `mode="paper"`)
and the offline backtest engine (`backtest/engine.py:_simulate_window`).

Whenever an identical tick stream and parameter set is fed to both engines, both must make identical
quoting, filling, chasing, stop-loss, and fresh-start decisions.

## 2. Parity Contract & Comparable Surface

Both engines process the same ticks. Parity is asserted on the decision-visible state surface:

| Key | Description |
|---|---|
| `entered` | Whether initial quotes were rested |
| `filled_up` | Whether the UP leg filled |
| `filled_down` | Whether the DOWN leg filled |
| `entry_price_up` | UP leg entry/fill price |
| `entry_price_down` | DOWN leg entry/fill price |
| `pair_captured` | Whether at least one pair was completed |
| `exit_taken` | Whether a stop-loss or dead-zone exit fired |
| `exit_side` | Which leg exited ("up", "down", or "") |
| `chased_leg` | Which leg was chased, if any ("up", "down", or "") |
| `pairs_count` | Number of completed pair merges across the window |
| `stops_count` | Number of stop-loss exits across the window |

Explicitly **not** compared (out of scope):
- `pnl_cents` vs `realized_pnl_usd` (different unit bases / share sizes).
- `fees_cents` (accounting detail).
- `settlement_mid`, `settle_source`, `class_label` (offline classification).

## 3. Harness Architecture (`tests/test_engine_parity.py`)

1. **`snaps_to_polls(snaps: list[dict]) -> list[tuple[float, dict]]`**:
   - Converts backtest tick/snap dicts into `(now, poll_data)` tuples compatible with `LiveTraderEngine._update_market_strategy`.
   - Constructs a synthetic `LiveMarket` with correct tokens, duration, and timestamps.
2. **`live_outcome(snaps: list[dict], params: BacktestParams) -> dict`**:
   - Spawns `LiveTraderEngine(load_persisted=False)` in `mode="paper"`.
   - Injects mock bridges to prevent network / wallet calls.
   - Applies `params` to engine configuration (offset, dead_zone_val, exit_thresh, enable_leg_chase, max_pair_cost, etc.).
   - Feeds each poll tick at its exact `now` timestamp.
   - Extracts the comparable surface from `mstate`.
3. **`backtest_outcome(snaps: list[dict], params: BacktestParams) -> dict`**:
   - Executes pure `_simulate_window(snaps, params)`.
   - Extracts the identical comparable surface from `WindowResult`.
4. **`assert_parity(snaps: list[dict], params: BacktestParams) -> None`**:
   - Runs both engines.
   - Compares the outcome dicts key by key.
   - On mismatch, raises `AssertionError` with a structured, crystal-clear diff showing the differing field, both values, and context.

## 4. Seed Scenarios

1. **Balanced Open to Merge**: Both legs fill and pair merges.
2. **Real Mid Anchor**: Opening quotes anchored to mid, not hardcoded 0.50 (#206).
3. **Pair-Cost Cap**: `max_pair_cost` caps chase without blocking quoting (#204).
4. **Unpriceable Leg**: Window skipped when book has no two-sided mid (#207).
5. **Stop-Loss Anchored to Entry**: Stop triggers based on drift from fill price (#209 / #230).
6. **Multi-Round Fresh Start**: Clean market re-enters and captures subsequent pairs outside dead zone (#232).

## 5. Acceptance Criteria

- [ ] `tests/test_engine_parity.py` implements `snaps_to_polls`, `live_outcome`, `backtest_outcome`, `assert_parity`.
- [ ] All seed scenarios pass cleanly with exact parity.
- [ ] Mismatch failure formatting is tested and confirmed human-readable.
- [ ] Test execution time for the entire parity module is under 5.0 seconds.
- [ ] `AGENTS.md` is updated to record `test_engine_parity.py` as the canonical gate.
- [ ] Zero regressions across existing targeted test suites.

## 6. Out of Scope

- Modifying existing strategy decision rules (all covered in #224–#233).
- Extracting shared live/backtest monolithic code into a separate library (deferred).
