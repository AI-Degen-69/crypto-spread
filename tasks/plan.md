# Plan — Issue #224: invariants (no invented numbers, one window clock)

**Size**: Standard (2 engine files + tests). **Type**: Code / Debug.
**Stack**: Python 3.12, FastAPI dashboard, pytest.

Spec: `SPEC.md`. Gates: `CONSTRAINTS.md`. Rule text: `docs/engine-decision-rules.md`.

## Locked interfaces

```python
# strategy/live_trader.py — LiveTraderEngine
def _window_clock(self, mstate: MarketLiveState, now: float) -> Optional[Tuple[float, float]]:
    """(window_length, elapsed) from market metadata, or None when there is no clock.

    Usable pair: start_ts and end_ts both finite and end_ts > start_ts.
    The sole definition of the window clock in the live engine.
    """
```

```python
# backtest/engine.py — module level
def _window_clock(first: dict) -> Optional[Tuple[float, float]]:
    """(start_ts, window_length) from the window's first snapshot, or None."""
```

Both return `None` rather than raising: a missing clock is an expected market condition, not a
programming error, and the callers must be able to skip and continue.

## Tasks

### T1 — live engine: one window clock, no slug parsing `[Backend/Logic]`

Files: `strategy/live_trader.py`.

1. Add `_window_clock` next to the other small helpers (near `_naked_timeout_elapsed`).
2. `:4483-4484` — replace the `(900.0 if "15m" in slug else 300.0)` expression and the
   `time_remaining_sec` fallback for `elapsed_sec` with one call. When it returns `None`: log
   once per window, set status `IDLE`, and return before any gate is evaluated.
3. `:5132` — `win_dur_naked` reads the same helper.
4. `:2628` — the dashboard's `win_duration_sec` uses `end_ts - start_ts` when usable and `0.0`
   otherwise. Display only; it never feeds a decision.

Verification: `tests/test_live_trader.py`, `tests/test_entry_timeout.py`.

### T2 — live engine: stop exit price resolves or holds `[Backend/Logic]`

Files: `strategy/live_trader.py`.

1. `_execute_stop_exit` `:1735` — drop the `0.40`. Order: the caller's `exit_price`, then the
   live book bid, then `_resolve_exit_bid(mstate, side)`.
2. `_resolve_exit_bid` raising `RuntimeError` means no executable mark exists. Catch it, log at
   `warning`, and return without exiting. `STOP_EXIT_PENDING` stays set, so the reconcile block
   at `:5093` retries on the next tick — the existing mechanism, not a new one.

Verification: `tests/test_stop_orders.py`, `tests/test_live_trader.py`.

### T3 — live engine: no invented re-entry mid `[Backend/Logic]`

Files: `strategy/live_trader.py`.

1. `:4744` — pass `mstate.mid` straight through; delete `or 0.50`.
2. `_maybe_reenter_drift_skipped` takes `Optional[float]` and returns `False` when it is `None`.
   A window with no mid cannot be judged against the drift band.

Verification: `tests/test_entry_timeout.py`.

### T4 — backtest: clock from timestamps only `[Backend/Logic]`

Files: `backtest/engine.py`.

1. Add `_window_clock(first)`. `_simulate_window` calls it once; `None` returns a `WindowResult`
   with reason `no_clock` and nothing traded.
2. Delete `elapsed = float(s_idx)` `:769`. `elapsed = cur_ts - start_ts`; a snapshot with no
   usable `ts` is skipped (`continue`).
3. Every time gate reads the derived `window_length`, not the snapshot's `duration` field:
   `:755`, `:760`, `:773`, `:891`, `:895`, `:899`, `:1126`, `:1168`.
4. `duration` stays only as the per-duration stop-threshold bucket key and as
   `WindowResult.duration`. It is a series label, and a comment says so.

Verification: `tests/test_backtest_engine.py`, `tests/test_sweep_backtest.py`.

### T5 — tests for each fabrication removed `[Test]`

Files: `tests/test_live_trader.py`, `tests/test_backtest_engine.py`.

1. Live: metadata with `end_ts <= start_ts` → window not traded, no order placed.
2. Live: a slug containing neither `5m` nor `15m`, with a valid 900s pair → gates measured
   against 900, proving nothing reads the name.
3. Live: stop exit with an empty book and no latched quote → position held, `exit_taken` still
   `False`, status still `STOP_EXIT_PENDING`.
4. Live: re-entry tick with `mstate.mid is None` → no re-entry.
5. Backtest: first snapshot with no usable pair → `no_clock`, untraded.
6. Backtest: a snapshot mid-window with no `ts` → skipped, and the gates of the surrounding
   snapshots are unaffected.

## Sequencing

T4 is independent of T1-T3 and can land first or last. T2 and T3 are independent of each other.
T5 follows whichever of its subjects has landed. One commit per task.

## Risks

- **Existing test fixtures build windows whose `end_ts - start_ts` is not the `duration` they
  declare.** `_window_snaps` pins `start_ts` but leaves `end_ts = ts + 298`, giving a 298s
  window where the test means 300. Gates at 10% move from 30.0s to 29.8s. Expected to be
  harmless, but every changed expectation gets explained rather than adjusted.
- **`snap()` stamps `start_ts = ts - 2.0`**, so windows starting at `ts <= 2.0` currently fall
  through to `float(s_idx)` and will now measure a real 2s offset. Same treatment.
