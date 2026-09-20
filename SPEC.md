# SPEC — Issue #272: Clarify Tick Files metrics and readiness progress

## Goal
Make the Tick Files page explain the capture state and show, for every file, how much data exists versus the two readiness milestones defined by Issue #273.

## User-facing integrity states
The API keeps raw `PASS`, `WARN`, and `FAIL` values for compatibility. The UI maps them to:

- `COMPLETE CAPTURE` — no detected structural or continuity problems.
- `PARTIAL CAPTURE` — readable data with gaps, collector errors, late starts, early cutoffs, or time reversals.
- `CORRUPTED DATA` — malformed/unreadable rows, missing required fields, invalid books/timestamps, or a read failure.

Every state includes a short reason and an action. `WARN` must never appear alone.

## Readiness targets
The UI and API expose measured values and both targets for every metric:

| Metric | EXPLORATORY | RESEARCH_READY |
|---|---:|---:|
| Valid tick snapshots | 1,000 | 10,000 |
| Independent market windows | 30 | 100 |
| Tape entries | 30 | 100 |
| Market-duration pairs | 1 represented pair | all 10 supported pairs |
| Windows per represented market-duration pair | 1 | 10 |
| Separate time blocks/days | 1 | 3 |
| Corrupt JSON rows | 0 | 0 |
| Schema error rate | ≤5% | ≤5% under the current policy |
| Sampling gaps | explicit numeric target and measured rate | ≤10% under the documented gap rule |
| Collector errors | explicit numeric target and measured count/rate | 0 unless a documented policy says otherwise |

Targets are project working milestones. They do not claim universal statistical validity or profitability.

## Progress contract
For each file and each metric, return/render:

- measured value;
- exploratory target and progress;
- research target and progress;
- reached/not reached state;
- next milestone and remaining amount when below the next target.

For lower-is-better metrics, progress is inverted or explicitly labeled `0 is the target`. Exceeding one metric never compensates for failing another readiness gate.

## Tooltip contract
The long explanation about targets and untouched later periods is hidden from the normal layout. An information icon beside Research Readiness opens a floating tooltip containing:

> The targets tell us whether this file contains enough varied data for the selected analysis. They do not prove that the strategy is profitable. Choose settings on one period and check them on a later period that was not used for choosing them.

## Compatibility
- Preserve raw integrity values, existing verification endpoints, cache/fingerprint behavior, rescans, and Backtest actions.
- Preserve the readiness level semantics and policy version from Issue #273 unless a threshold change is explicitly documented and tested.
- Keep market labels duration-first with two-digit minutes (`05m BTC`, `15m BTC`).

## Edge cases
- Empty or tiny files show zero-valued progress and the next exploratory milestone.
- Zero-window files show `0` for tape entries per window without division errors.
- Lower-is-better metrics never render larger error counts as healthier progress.
- Missing readiness data from an old cache is treated as stale and does not produce partial UI claims.
- A file with enough snapshots but insufficient markets, days, or quality checks remains below the appropriate readiness level.

## Out of scope
Collector protocol changes, replay/fill/exit math, market-window grouping changes, live trading behavior, and claims that any threshold proves live profitability.
