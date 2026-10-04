# ADR-0004: Retire strategy/config.py MakerConfig as Executable Configuration Surface

**Date**: 2026-10-04
**Status**: accepted
**Deciders**: AI Degen, Antigravity

## Context

`strategy/config.py` was a 778-line configuration module containing `MakerConfig` with 94 fields, derived properties, and an environment-driven `load()` function.
Neither execution engine instantiated or consumed `MakerConfig`:
1. `LiveTraderEngine.__init__` in [`strategy/live_trader.py`](file:///strategy/live_trader.py) defines and manages live/paper execution parameters directly.
2. `BacktestParams` in [`backtest/engine.py`](file:///backtest/engine.py) defines simulation parameters.

The only importer was an unexecuted scratch block in `strategy/markets.py:413-427` which failed at runtime with `AttributeError` (calling non-existent `cfg.gamma_host`). Furthermore, `MakerConfig` defaults (notably `max_pair_cost = 0.995`) were cited as live facts in Issue #421, when the actual engine default is `0.99`.

## Decision

We retire `strategy/config.py` as an executable configuration surface.
1. Historical measurements and rationale are preserved in [`docs/maker-config-legacy-rationale.md`](file:///docs/maker-config-legacy-rationale.md).
2. `strategy/config.py` is stripped to a legacy non-runtime reference stub with loud deprecation docstrings pointing to `LiveTraderEngine` and `BacktestParams`.
3. The broken `load()` function and the dead `__main__` block in `strategy/markets.py` are removed.
4. An automated test guard (`tests/test_legacy_maker_config.py`) ensures no code outside `strategy/config.py` imports `MakerConfig` or `strategy.config`, and that no engine adopts the legacy `0.995` default.

## Alternatives Considered

### Alternative 1: Delete `strategy/config.py` Completely
- **Pros**: Cleanest diff, zero lines in `strategy/config.py`.
- **Cons**: Breaks any documentation citation or historical scratch script that might import `MakerConfig`.
- **Why not**: Retaining a stripped, clearly marked legacy dataclass preserves backwards-compatibility for inspection while eliminating all live-looking execution surfaces and dead helper methods.

### Alternative 2: Keep the File Intact and Only Add a Header Comment
- **Pros**: Minimal diff.
- **Cons**: Leaves 94 live-looking parameters, dead helper methods, broken `load()` injection, and potential for confusion.
- **Why not**: A passive comment does not stop agents or readers from treating `MakerConfig` fields as runtime defaults.

## Consequences

### Positive
- Configuration ownership is unambiguous: `LiveTraderEngine` and `BacktestParams`.
- Eliminates risk of quoting outdated defaults (`0.995` vs `0.99`).
- Removes dead code and broken `__main__` scripts.
- Guard tests enforce this boundary going forward.

### Negative
- Anyone looking for configuration in `strategy/config.py` must be directed to `LiveTraderEngine` or `BacktestParams`.

### Risks
- Stale documentation links. Mitigated by updating `AGENTS.md` and documentation citations to point to this ADR and `docs/maker-config-legacy-rationale.md`.
