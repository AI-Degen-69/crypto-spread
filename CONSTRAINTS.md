# Constraints — Issue #272: Tick Files clarity and readiness progress

## Scope
- Change Tick Files metric semantics, customer-facing status labels, readiness target presentation, tooltip copy, and focused tests only.
- Preserve collector behavior, replay mathematics, window grouping, raw API compatibility, verification cache behavior, rescans, and Backtest actions.
- No new dependencies.

## Correctness
- Raw integrity values remain `PASS`, `WARN`, and `FAIL` in machine-readable responses.
- Customer-facing labels describe capture state: `COMPLETE CAPTURE`, `PARTIAL CAPTURE`, and `CORRUPTED DATA`.
- Every status includes a reason and safe action; never display bare `WARN`.
- Use the versioned readiness policy from Issue #273 as the source of truth for targets.
- Every metric exposes both `EXPLORATORY` and `RESEARCH_READY` targets, including quality metrics.
- A readiness level remains gated by all required checks; progress bars cannot promote a file by averaging unrelated metrics.
- Lower-is-better metrics use inverted or explicitly zero-target progress semantics.
- Small, empty, old-cache, and zero-window reports must render deterministically without division errors.

## UX and accessibility
- Main Tick Files content remains concise; the policy/profitability explanation appears only in an accessible floating tooltip.
- Tooltip must be reachable by keyboard, have an accessible name, and close on Escape or outside interaction.
- Progress rows expose text values in addition to visual bars; color is never the only status signal.
- Keep supported responsive widths and avoid horizontal overflow.
- Preserve canonical market labels: `05m BTC`, `15m BTC`, etc.

## Performance
- Do not rescan large files merely to render the new labels or bars.
- Keep verification streaming and cache-first.
- Do not materially increase the manifest response payload with duplicated raw data.

## Testing and anti-cheat
- Add/adjust tests for status mapping and reasons, tooltip-only copy, both target sets, next-milestone calculations, zero values, lower-is-better direction, old-cache handling, and existing endpoint regressions.
- Run only targeted suites during development; CI remains the full-suite gate.
- Do not skip, weaken, delete, or suppress tests.
- Do not change thresholds silently; update policy and tests together if needed.
