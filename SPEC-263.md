# SPEC — Issue #263: Strategy Geometry Preview

## Goal

Make the Backtest tab's Strategy Geometry Preview answer “what will my params quote?” at a glance through a clean, readable price × time SVG without changing strategy behavior.

## Acceptance criteria

1. At default values (`offset=0.02`, stop `0.05`, reversal `0.02`), right-side price labels are legible, do not overlap, and use clear leader lines or deliberate staggering.
2. The preview uses a display-face hierarchy for headings and a mono face for numeric values, with a consistent 9–12px scale.
3. The chart remains proportional and readable at 1200–1920px widths.
4. Quotable corridor, entry delay, dead zone, resting quote levels, and exit levels remain visually distinguishable.
5. The legend contains no more than five grouped entries.
6. The computed levels (`longBid`, `shortComp`, `stopPrice`, `revPrice`, `pairCost`) remain unchanged for the same inputs.
7. `python -m pytest tests/test_osc_dash_integration.py -q` passes.

## Edge cases

- Equal or near-equal levels must still receive separate readable labels.
- Zero offset, stop, reversal, delay, and dead-zone values must remain valid inputs.
- Large stop/reversal values and quote ranges near 0 or 1 must stay inside the SVG gutter and plot bounds.
- A blocked active window must retain a clear warning instead of rendering misleading quote lines.

## Explicitly out of scope

- Strategy math, backtest engine, parameter definitions, and defaults.
- Other dashboard tabs, equity curve, histogram, API behavior, and chart libraries.
- New dependencies.
