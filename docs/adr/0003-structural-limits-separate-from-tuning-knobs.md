# ADR-0003: Structural limits are a separate parameter class from tuning knobs

**Date**: 2026-09-16
**Status**: accepted
**Deciders**: operator, Claude (issue #233)

## Context

`BacktestParams` treats every field as one kind of thing. The registry groups them for display
(`trading_knobs`, `execution_assumptions`, `window_policy`), but nothing distinguishes a value
meant to be swept from a bound that exists to make a class of action impossible.

The cost of that is already in the repo. `max_pair_cost` caps what the leg chase may pay for a
completed pair; a binary pair settles at 1.00, so a cap above 1.00 authorises a guaranteed loss.
The live engine clamps it to 1.00. The backtest's equivalent defaults to **1.05** — deliberately
above the maximum live will accept — purely so a research sweep could push it past the top of
its range and switch the gate off. That divergence was then documented as intentional and lived
in the code for months.

## Decision

Parameters carry a class. **Tuning knobs** are swept and tuned freely: offset, stop loss, entry
delay, share size. **Structural limits** bound what the engine may do at all: `max_pair_cost`,
`quote_range`, the dead zone. Both are changeable; only the first is part of the tuning set, and
the UI renders them apart.

## Alternatives Considered

### Alternative 1: Leave one flat parameter set
- **Pros**: No work. The existing display groups already give the UI something to render.
- **Why not**: The display groups are about layout, not authority. `pair_cost_gate` sat in
  `trading_knobs` alongside the offset, which is exactly how it came to be swept to a
  self-disabling value without anyone objecting.

### Alternative 2: Hard-code the structural limits, with no knob at all
- **Pros**: Strongest guarantee. Nothing can disable them.
- **Why not**: The operator wants them changeable — the quotable range and the pair cost are
  judgements that may need revisiting, and the dead-zone unit is an open measurement (#222).
  Hard-coding would force a code change to answer a research question. Compare ADR-0002, where
  the value genuinely is a fact about the venue and hard-coding *is* correct.

### Alternative 3: Enforce with validation only — clamp and move on
- **Why not**: Clamping is what live already does, and the backtest simply declared a different
  range instead. The problem is not that a bad value gets through; it is that nobody could see
  the knob was a different kind of thing.

## Consequences

### Positive
- A sweep cannot silently disable a safety bound; sweep drivers refuse a structural limit unless
  told explicitly.
- The declared divergence over `pair_cost_gate`'s range disappears — with the class made
  explicit, both engines share one name, one default and one range.
- The operator reads the two groups differently, which is how they were always meant to be read.

### Negative
- Another dimension in the registry to keep correct, and `tests/test_param_registry.py` grows a
  case requiring every field to declare a class.
- Existing sweep drivers that vary a structural limit need an explicit opt-in.

### Risks
- **The class becoming decorative** — declared in the registry but ignored by the code that
  matters. Mitigated by putting the enforcement in the sweep drivers and the API clamp, not only
  in the UI.
