# ADR-0001: A parity harness, not a shared decision module

**Date**: 2026-09-16
**Status**: accepted
**Deciders**: operator, Claude (issue #214)

## Context

The strategy is implemented twice: `strategy/live_trader.py` trades it and `backtest/engine.py`
simulates it. They share knob *names* through `BacktestParams.param_spec()`, which is what makes
divergence invisible — the dashboard shows one name and one number for two different meanings.

Four divergences were found by hand (#204, #206, #207, and the backtest ending a window at the
first merge), each by someone reading both files side by side. Nothing in the repo would have
caught any of them. Issue #214 asked for either a single source of truth for the shared logic or
an executable parity check, explicitly leaving the choice until after an audit.

## Decision

We build a parity harness that drives both engines over the same book sequence and compares
their decisions, and we do not extract the shared logic into a common module.

## Alternatives Considered

### Alternative 1: Extract the shared decision logic into one module
- **Pros**: Removes the class of defect entirely rather than detecting it. One implementation
  cannot disagree with itself.
- **Cons**: `_update_market_strategy` is ~1,000 lines entangled with CLOB calls, WebSocket
  state, engine locks, telemetry and order placement; `_simulate_window` is ~560 pure lines.
  Separating decision from execution inside the live engine is the actual work, and it is large.
- **Why not**: The blast radius is live trading with real money, and the extraction would have
  to be done against rules that were, at the time, still wrong in both engines. Deferred, not
  rejected — the rules document is the map it starts from.

### Alternative 2: A configuration mirror — assert both engines are configured identically
- **Pros**: Cheap. `scripts/replay_shadow_check.py` already does a version of it.
- **Why not**: It asserts the wrong thing. The operator deliberately runs live on one parameter
  set while sweeping freely in research; identical *values* are not wanted and never will be.
  What must match is the decision logic, which a config mirror cannot see.

### Alternative 3: Do nothing; rely on review
- **Why not**: Review is what produced the four known divergences, over months.

## Consequences

### Positive
- Each rule change lands in both engines with a test that fails if only one of them changed.
- The harness is an extension point: every rule issue (#224-#233) adds its own scenarios rather
  than inventing a second way to compare engines.
- The two engines keep independent parameter values, which is a requirement, not a compromise.

### Negative
- The duplication remains. Parity is detected, not prevented, so a divergence still ships until
  a scenario covers it.
- Coverage is only as good as the scenarios written.

### Risks
- **A scenario that pins today's tuning instead of the shared rule.** Mitigated by a constraint:
  scenarios compare the two engines against each other, never against a hard-coded expected
  value.
- **The harness becoming the thing that must be edited for every rule change.** Mitigated by
  forbidding knob and model names in the harness — it reads the engines' current configuration.
