# ADR-0002: One hard-coded fill rule; `fill_model` is not a knob

**Date**: 2026-09-16
**Status**: accepted
**Deciders**: operator, Claude (issue #226)

## Context

The backtest offered four fill models — `tape`, `book`, `both`, `cross` — selectable per run and
rendered in the dashboard. The shipped preset used `tape`.

The live engine has no such setting. In paper mode it fills from two sources at once: a
WebSocket tape print within tolerance of our price, and the book touching our price. That is the
`both` model, and no preset ever selected it.

Issue #205 measured the consequence without naming it: over 550 windows, `tape` filled 3.6%
where `cross` filled 57% — a 16x gap treated as a mystery in the backtest, when it was the
backtest running half of the live engine's fill mechanism.

## Decision

There is one fill rule in both engines and it is not configurable. A resting buy fills when a
trade prints at our price **or** the best ask passes fully through it. Both are detectors of the
same event. The fill price is always our resting price and an entry never pays a fee: the entry
quote is a limit order that waits, so whoever filled it was the aggressor, and the aggressor
pays. Exits, which sell into the bid, are takers and keep paying the fee.

An earlier draft of this ADR had the second detector book the ask price and charge a taker fee,
reading "ask below our bid" as "we crossed into it". Corrected by the operator on 2026-09-16: an
ask resting below our bid is not a state a book can hold, so seeing it in a one-second snapshot
is evidence that our order was taken, not that we took anything.

## Alternatives Considered

### Alternative 1: Keep the four models, fix the preset to `both`
- **Pros**: No removal work. Research keeps its comparison tool.
- **Cons**: Leaves a setting whose wrong values silently describe an engine that does not exist.
- **Why not**: The operator's framing settled it — the fill model is a statement about how the
  venue behaves, not an operator choice. A knob invites a value that is simply false.

### Alternative 2: Hard-code `cross` (fully-through book only)
- **Pros**: Simplest; most conservative.
- **Why not**: The tick capture is incomplete — the collector does not observe every trade in
  every second — so the book check alone misses fills that really happened. The tape covers what
  the book missed and vice versa.

### Alternative 3: Hard-code `book` (touch), matching live exactly as it is today
- **Why not**: A touch does not mean we traded; other bids may sit ahead of ours in the queue.
  The operator chose the fully-through rule knowing it makes the live engine fill *less* often
  than it does now. A backtest should promise fewer fills than the market gives, never more.

## Consequences

### Positive
- The backtest can no longer be configured into a model the live engine never runs.
- Issue #205's 16x gap is explained and closed rather than investigated further.
- Fees fall out correctly and simply: entries are limit orders and pay none; exits cross the
  book and pay the venue's taker fee, as they already did.

### Negative
- Removal touches 24 files. Research sweeps that pass `fill_model` break.
- Historical sweep results are no longer reproducible with current code — the numbers in
  `docs/ev-research-findings-2026-09-11.md` describe a fill rule that no longer exists.
- The live engine fills slightly less often than before, accepted knowingly.

### Risks
- **Orphaning the research evidence trail.** The eight phase drivers are deleted rather than
  frozen: they swept `fill_model`, so with the knob gone they cannot be imported, let alone run,
  and `RESULTS-ARE-STALE.md` had already recorded that their output could not be regenerated.
  Mitigated by keeping their `*.json` tables, which `docs/ev-research-findings-2026-09-11.md`
  cites as the record of how `patient_band_maker` was chosen, and by saying plainly in both that
  no row in them can be reproduced. `ev_lab.py`, `sim2.py` and `selection_bias.py` are under
  test and are updated instead.
- **Losing a comparison tool research genuinely used.** Accepted: comparing a model against a
  model the engine does not run was never evidence about this system.
