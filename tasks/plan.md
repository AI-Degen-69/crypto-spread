# Plan — issue #164: one parameter contract for Backtest and Cockpit

**Size: Large.** Cross-cutting: `backtest/engine.py` (new simulated behaviour),
`server/osc_dash.py` (two UIs + two API schemas), plus tests. **Type: Code +
Design/UI.** Stack: Python 3.12 / FastAPI, `pytest` (baseline **713 passed**).

## What the issue got right, and what it missed

Verified every claim against the code first — three issues this week had stale
premises.

**Stale in the issue:**
- "`exit_reversal` is hardcoded to `0.50` in `BacktestParams`." It is
  `exit_reversal: float = 0.02` (`backtest/engine.py:123`). The `0.50` is the
  market midpoint it compares against, not the default.
- "`strategy/live_trader.py:MakerConfig`." There is no `MakerConfig`. Live
  config is `update_config()` kwargs plus `LIVE_PRESETS`.

**Understated in the issue.** It names one missing engine knob. There are
four, and the drift runs both ways:

| knob | Backtest UI | Backtest engine | Cockpit UI | Live engine |
|---|---|---|---|---|
| `naked_leg_timeout_pct` | — | **absent** | ✅ | ✅ |
| `exit_thresh_naked` | — | **absent** | ✅ | ✅ |
| `stop_loss_enabled` | — | **absent** | preset only | ✅ |
| `enable_leg_chase` | — | **absent** | preset only | ✅ |
| `exit_reversal` | **absent** | ✅ | ✅ | ✅ |
| `entry_timeout_pct` | **absent** | ✅ | ✅ | ✅ |
| `entry_delay_sec` | ✅ | ✅ | **absent** | ✅ |
| `entry_band` | ✅ | ✅ | **absent** | ✅ |
| `reentry_drift_band` | ✅ | ✅ | **absent** | ✅ |
| `min_requote_remaining_sec` | ✅ | ✅ | **absent** | ✅ |
| `pair_cost_gate` / `max_pair_cost` | ✅ | ✅ | **absent** | ✅ |

The sharp one: **`patient_band_maker` sets `stop_loss_enabled=False`, and the
backtest engine has no such knob.** The backtest expresses hold-to-settlement
by setting exits to 0.49/0.50 — a threshold that never trips — while live
expresses it with a boolean. Two mechanisms for one intent, neither proving the
other. This is the same defect class as #182 finding 6, one level up.

And the Cockpit has no input for `entry_delay_sec` or `entry_band` at all — the
two knobs that *define* the winning preset. They are reachable only through the
preset, so an operator cannot tune or even see them live.

## Approach

`backtest/engine.py:172` already carries `_PARAM_GROUPS`: a registry of
`(field, label, why)`. Extend it into the single source of truth — label, unit,
default, bounds, and which surfaces expose each knob — and have both UIs and
both API validators read from it. Hand-matching label strings in two files is
what let them drift; it would drift again.

## Tasks

- [x] **1. `[Backend/Logic]` Registry becomes the contract.** Extend
  `_PARAM_GROUPS` entries to carry `unit`, `default`, `bounds`, and
  `surfaces: {"backtest", "cockpit"}`. Add `BacktestParams.param_spec()`
  returning it. No behaviour change.
  *Skills:* `api-and-interface-design`, `test-driven-development`.
  *Verify:* new `tests/test_param_registry.py` — every `BacktestParams` field
  appears exactly once; bounds match `__post_init__` validation.

- [x] **2. `[Backend/Logic]` `stop_loss_enabled` in the engine.** Add to
  `BacktestParams` (default `True` = today). When `False`, `_simulate_window`
  holds a filled naked leg to settlement instead of taking the stop exit —
  mirroring `live_trader.py:1395`.
  *Verify:* default replay is bit-identical to master on a fixture; `False`
  path proves the naked leg reaches settlement.

- [x] **3. `[Backend/Logic]` `naked_leg_timeout_pct` + `exit_thresh_naked`.**
  Mirror `_naked_timeout_hit` (`live_trader.py:4045-4054`): measured from the
  moment the leg went naked, not window open; `0.0` disables.
  `exit_thresh_naked` defaults to `None` → falls back to `exit_thresh`.
  *Verify:* a naked leg times out at the right tick; `0.0` changes nothing.

- [x] **4. `[Backend/Logic]` `enable_leg_chase`.** Port the chase rule already
  proven in `research/sweeps/sim2.py` (`chase_cap`) into the canonical engine,
  default `False` = today's behaviour.
  *Verify:* parity against `sim2`'s chase on the same window.

- [x] **5. `[Design/UI]` Backtest tab reads the registry.** Render its inputs
  from `param_spec()`; adds the missing `exit_reversal` and
  `entry_timeout_pct`, plus the four new knobs. Delete hard-coded labels.
  *Verify:* served-HTML assertions + live DOM read at `:8802`.

- [x] **6. `[Design/UI]` Cockpit tab reads the registry.** Same, adding
  `entry_delay_sec`, `entry_band`, `reentry_drift_band`,
  `min_requote_remaining_sec`, `max_pair_cost`. Keep the running-bot lock
  (`cockpitParamsLockHint`) on every new input.
  *Verify:* served-HTML + live DOM; a test asserts no shared label is
  hard-coded outside the registry.

- [x] **7. `[Backend/Logic]` Schema sync.** `/api/backtest` and
  `/api/live/config` validate against the registry rather than ad-hoc parsing,
  so an out-of-range value is refused identically on both.
  *Verify:* parametrised bounds tests per knob on both endpoints.

## Improvement proposed and adopted

Operator chose the registry over flat label-matching, and chose to land the
four engine knobs now — before the post-capture backtest — so that run measures
the configuration the bot actually executes.
