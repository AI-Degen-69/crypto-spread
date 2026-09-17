# SPEC — Issue #230: Rule: one stop threshold — delete `exit_thresh_naked`

Binding while `feat/one-stop-threshold-230` is live. Per-issue working file (`docs/git-workflow.md` §5)
— not an architecture document.

The rule itself is agreed: `docs/engine-decision-rules.md` §2 (`stop_loss`) and §10
(`naked_leg_stop`). That file is the definition; this spec is the work that makes
the code match it.

## Goal

Unify the stop-loss mechanism across both engines to exactly one threshold:
> **There is one stop threshold, not two.** `exit_thresh_naked` is deleted from both engines.
> A completed pair cannot lose (`1 - 2*offset` settles at 1.00); there is nothing for a paired stop to protect.

Delete the redundant, dead-code `exit_thresh_naked` parameter from both engines, parameter
registries, API schemas, and dashboard UI, establishing a single authoritative stop-loss rule.

## Acceptance Criteria

1. **One Stop Threshold in Both Engines (`strategy/live_trader.py` & `backtest/engine.py`):**
   - Live engine: Governed strictly by `self.exit_thresh` (default 0.05).
   - Backtest engine: Governed strictly by `params.exit_thresh(slug, duration, series=series)` (from `exit_thresh_by_slug`).
   - `exit_thresh_naked` and all helper wrappers (`_naked_exit_thresh`, `naked_exit_thresh`) are completely removed.
2. **Identical Stop Arming (`docs/engine-decision-rules.md` §2):**
   - Armed the moment an entry leg fills.
   - Price calculation:
     `stop_price = clamp(round(entry_fill_price - stop_threshold, 2), 0.01, 0.99)`
   - Held in memory (`STAGED`), zero venue exposure until trigger.
3. **Identical Stop Trigger Math:**
   - Adverse excursion measured from leg's actual entry price, rounded to 6 decimal places:
     - Filled UP leg: `excursion = round(entry_price - mid, 6)`
     - Filled DOWN leg: `excursion = round(mid - (1.0 - entry_price), 6)`
   - Stop fires when `max_excursion >= exit_thresh` and no reversal is seen (`excursion < exit_reversal`).
4. **Identical Action on Trigger (OCO):**
   - Cancel opposite resting buy order.
   - Sell filled leg at best executable bid (crosses book, pays taker fee in backtest; zero fallback to fabricated prices like 0.40).
   - If opposite leg fills before stop trigger fires, cancel staged stop and complete pair merge. Exactly one outcome occurs.
   - If no executable bid is available, hold and re-evaluate on the next tick.
5. **Full Surface Deletion of `exit_thresh_naked`:**
   - `backtest/engine.py`: Removed from `BacktestParams`, `param_spec`, `_PARAM_GROUPS`, and `__post_init__`.
   - `strategy/live_trader.py`: Removed from constructor, `get_state()`, `update_config()`, and internal drift/exit checks.
   - `server/osc_dash.py`: Removed from `/api/backtest` query params, `/api/live/config` schemas, HTML inputs (`btExitNaked`, `cockpitExitNaked`), and JavaScript bindings.
   - `research/sweeps/ev_lab.py`: Removed from `ENGINE_ONLY_KNOBS`.
   - `scripts/replay_shadow_check.py`: Updated assertion.
6. **Executable Parity Harness (`tests/test_stop_loss_parity.py`):**
   - Parity 1: Identical stop arming calculation.
   - Parity 2: UP leg fill adverse drift triggers stop at the identical tick and cancels DOWN order (OCO).
   - Parity 3: DOWN leg fill adverse drift triggers stop at the identical tick and cancels UP order (OCO).
   - Parity 4: Reversal detection suppresses stop identically when price retraces.
   - Parity 5: Second leg fill completes pair and cancels staged stop identically.
7. **Zero Regressions:**
   - All targeted test suites (`test_backtest_engine.py`, `test_live_trader.py`, `test_param_registry.py`, `test_osc_dash_integration.py`, `test_stop_orders.py`, etc.) pass cleanly.

## Out of Scope

- Leg chase escalation ladder (#231): Chasing unfilled leg with elapsed time belongs to #231.
- Fresh start cleanup (#232): Multi-round replay without memory belongs to #232.
- Parity test harness for remaining unmigrated rules (#214): Belongs to #214 after #230, #231, and #232 land.

## Edge Cases

- No executable bid at trigger: Engine waits for next tick; no fabricated fallback.
- Exact floating point boundary: 6dp rounding prevents `0.0499999999999` from skipping stop when threshold is `0.05`.
- Reversal boundary: Excursion re-entering `exit_reversal` latches `reversal_seen` and suppresses stop exit.
