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
trade prints at our price **or** the best ask passes fully through it. The fill price depends on
which side of the trade we were on: our price when we were hit as maker, the ask price when we
crossed as taker.

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
- Fees fall out correctly: maker fills pay none, taker fills pay the venue's taker fee.

### Negative
- Removal touches 24 files. Research sweeps that pass `fill_model` break.
- Historical sweep results are no longer reproducible with current code — the numbers in
  `docs/ev-research-findings-2026-09-11.md` describe a fill rule that no longer exists.
- The live engine fills slightly less often than before, accepted knowingly.

### Risks
- **Orphaning the research evidence trail.** Mitigated by leaving `research/sweeps/phase*.py`
  frozen and untouched, with a note in `RESULTS-ARE-STALE.md` recording that they predate this
  rule. `ev_lab.py` and `selection_bias.py` are under test and so are updated.
- **Losing a comparison tool research genuinely used.** Accepted: comparing a model against a
  model the engine does not run was never evidence about this system.
