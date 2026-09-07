# tasks/plan.md — Issue #95: re-entry into a drift-skipped window

Branch: `feat/issue-95-drift-reentry` (off `master`)
Spec: `SPEC.md` · Constraints: `CONSTRAINTS.md`
Baseline: 317 tests green.

## Interfaces locked before coding

`MarketLiveState` (`strategy/live_trader.py:385-402`), new fields:
```python
reentry_count: int = 0
reentry_mid: Optional[float] = None
reentry_drift: Optional[float] = None
```

`LiveTraderEngine.__init__` (`strategy/live_trader.py:534-548`), new attributes:
```python
self.reentry_drift_band: float = 0.015
self.min_requote_remaining_sec: float = 60.0   # shared knob, issue #89 adopts it
self.max_reentries_per_window: int = 1
```

New private helper on `LiveTraderEngine`:
```python
def _maybe_reenter_drift_skipped(
    self,
    mstate: MarketLiveState,
    slug: str,
    mid: float,
    remaining_sec: float,
    book_two_sided: bool,
    is_late_start: bool,
) -> bool:
    """Return True when a DRIFT_SKIPPED window was re-entered on this tick."""
```

`BacktestParams` (`backtest/engine.py:121-134`), new fields with identical defaults:
```python
reentry_drift_band: float = 0.015
min_requote_remaining_sec: float = 60.0
max_reentries_per_window: int = 1
```

`update_config()` signature gains `reentry_drift_band: Optional[float] = None`;
`params` payload gains `"reentry_drift_band": self.reentry_drift_band`;
`ConfigPayload` (`server/osc_dash.py:880`) gains
`reentry_drift_band: Optional[float] = Field(default=None, ge=0.0, le=0.5)`.

---

## Tasks

### T1 — live state + knobs (no behavior change yet)
Files: `strategy/live_trader.py`
Add the three `MarketLiveState` fields and the three engine attributes with the
comment block explaining the shared `min_requote_remaining_sec` knob and the
static-anchor caveat. Clear the three new state fields in
`_handle_window_rollover()` (`:3556-3568`), in `reset_pnl()` (`:2530-2536`) and in
`_clear_market_order_state()` if it already clears window state.
Accept: `python -m pytest tests/test_live_trader.py -q` still green.

### T2 — RED: live re-entry tests
Files: `tests/test_live_trader.py` (extend the issue #92 block after `:1733`)
Reuse `_drift_engine()` / `_drift_poll_data()`. Build every payload anchored at the
base `now` and vary only the tick time passed to `_update_market_strategy`, so
`start_ts` / `end_ts` stay fixed and `remaining_sec` is controllable.
Tests: revert-inside-band re-enters; stays-outside-band does not; timeout-cancelled
never re-enters; #96 late-start-skip never re-enters; below
`min_requote_remaining_sec` does not; cap enforced on second revert; snapshot +
`last_action` telemetry after re-entry; rollover and `reset_pnl` clear
`reentry_count`.
Accept: the new tests fail for the right reason (`status` still `DRIFT_SKIPPED`).

### T3 — GREEN: live re-entry path
Files: `strategy/live_trader.py`
Add `_maybe_reenter_drift_skipped()` and call it in `_update_market_strategy`
immediately after the cancellation block (`:3159`) and before `can_place_entry`
(`:3161`), then re-read `is_adverse_open = mstate.adverse_open` so placement sees
the cleared flag on the same tick. Compute
`remaining_sec = max(0.0, mstate.end_ts - now)` falling back to
`max(0.0, win_duration - elapsed_sec)`. All mutations under `_engine_lock`; log at
`info` mirroring the DRIFT_SKIPPED log line.
Accept: `python -m pytest tests/test_live_trader.py -q` fully green.

### T4 — params payload + runtime setter + dashboard config
Files: `strategy/live_trader.py`, `server/osc_dash.py`
Expose `reentry_drift_band` in the params payload, add it to `update_config()`
(including the running-engine `param_changed` guard), and pass it through from
`ConfigPayload`.
Accept: `python -m pytest tests/test_osc_dash_integration.py -q` green; a config
POST with `reentry_drift_band` while stopped changes `state["params"]`.

### T5 — RED: backtest parity tests
Files: `tests/test_entry_timeout.py` (append after the #92 parity block, `:455`)
Tests: adverse-skipped window whose later snapshot mid returns inside the band
fills; the same window with the mid staying outside does not; a
timeout-cancelled window with mid 0.50 never fills; re-entry below
`min_requote_remaining_sec` does not fill.
Accept: new tests fail (`filled_up is False` where True is expected).

### T6 — GREEN: backtest split flag + re-entry
Files: `backtest/engine.py`
Add the three `BacktestParams` fields plus `__post_init__` validation. In
`_simulate_window()` add `adverse_skipped = False`, set it only where the gate
fires (`:344-346`), add a `reentry_count` local, and apply the re-entry rule per
tick after `mid` is computed using `remaining = duration - elapsed`.
Accept: `python -m pytest tests/test_entry_timeout.py tests/test_backtest_engine.py -q` green.

### T7 — full suite + docstring gate
Accept: `python -m pytest -q` green (317 + new). `tests/test_docstrings.py` passes
(every new public/dataclass member documented).
