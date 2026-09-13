# TODO — issue #164

Baseline before any change: `python -m pytest -q` → **713 passed**.

- [x] 1. `[Backend]` Extend `_PARAM_GROUPS` into a full spec; add `param_spec()`
- [ ] 2. `[Backend]` `stop_loss_enabled` in `_simulate_window` (default True = today)
- [ ] 3. `[Backend]` `naked_leg_timeout_pct` + `exit_thresh_naked` (default off)
- [ ] 4. `[Backend]` `enable_leg_chase` ported from `sim2.py` (default False)
- [ ] 5. `[UI]` Backtest tab renders from the registry (+ exit_reversal, entry_timeout)
- [ ] 6. `[UI]` Cockpit tab renders from the registry (+ delay, band, reentry, pair cost)
- [ ] 7. `[Backend]` `/api/backtest` and `/api/live/config` validate from the registry

Gate on every task: defaults reproduce master exactly; full suite green.
