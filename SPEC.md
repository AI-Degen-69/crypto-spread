# SPEC — Issue #269: Duration-aware backtest timing percentages

## Goal
Represent Backtest timing controls as percentages of each market window and render Strategy Geometry on a normalized 0–100% time axis, so identical settings mean the same relative position for 5m and 15m markets.

## Interface contract
- Backtest UI controls for late entry and dead zone accept `0..100%` values.
- `10%` means 30 seconds for a 300-second window and 90 seconds for a 900-second window.
- Existing timestamp-based simulation remains authoritative; conversion to seconds must use each window's actual `end_ts - start_ts`.
- The API may preserve internal `entry_delay_sec`/`dead_zone_val` fields for compatibility only if the Backtest UI/API boundary clearly converts percentages per duration and never applies one fixed 5m delay to 15m windows.
- The Backtest tab no longer exposes a seconds-unit selector for these controls.
- Strategy Geometry's horizontal axis is normalized elapsed percentage: 0%, 10%, …, 100%; labels and shaded regions use the same percentage model.

## Acceptance criteria
- Geometry has no hardcoded 300-second primary axis.
- Entry Delay and Dead Zone are percentage controls with clear labels, bounds, defaults, and validation.
- Mixed 5m/15m replay applies the same percentage independently to each window.
- 0% disables the corresponding delay/dead-zone behavior; 100% produces the documented fully delayed/blocked boundary behavior.
- Existing backtest results, safe file handling, explicit-run behavior, and concurrency guard remain intact.
- Targeted engine, parity, API, and served-HTML tests pass.

## Edge cases
- Missing/invalid/non-finite percentages use the documented safe fallback or return validation errors; no NaN reaches the worker.
- Window lengths are derived from timestamps, not snapshot count or series label.
- Partial windows and windows with invalid clocks retain existing handling.
- Both 5m and 15m geometry renderings remain readable and use the same percentage labels.

## Out of scope
Live cockpit parameter semantics, strategy entry/exit math beyond time-unit conversion, tick data, sweep axes, chart expansion, and unrelated dashboard tabs.
