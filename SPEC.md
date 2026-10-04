# Specification — Issue #426: Retire strategy/config.py MakerConfig as executable surface

## 1. Goal
Retire `strategy/config.py` (`MakerConfig` and `load()`) as an executable configuration surface, preserving its measured rationale in durable documentation (`docs/maker-config-legacy-rationale.md` and ADR 0004). Remove the broken caller in `strategy/markets.py`. Prevent future regressions or misquotations of legacy defaults (such as `max_pair_cost = 0.995`) as live engine settings.

## 2. In Scope
- Preserve historical hunter-fleet rationales in `docs/maker-config-legacy-rationale.md`.
- Record the architectural decision in `docs/adr/0004-retire-maker-config.md`.
- Strip `strategy/config.py` of `load()`, `# hook probe` lines, and dead helper methods; add loud docstrings and legacy markers identifying that it is non-runtime and that `LiveTraderEngine.__init__` and `BacktestParams` own configuration.
- Remove the broken `if __name__ == "__main__":` block in `strategy/markets.py:413-427`.
- Create a test guard `tests/test_legacy_maker_config.py` asserting no live code imports `MakerConfig` or `strategy.config`, `load` is absent, and docstring warnings exist.
- Update documentation references (`AGENTS.md:50`, `docs/operations.md`, `docs/research-spread-bot-conclusions.md`).

## 3. Out of Scope
- No runtime default moves: `LiveTraderEngine` defaults (including `max_pair_cost = 0.99`), `BacktestParams`, and CLI defaults remain untouched.
- No changes to parameter registries, `params_hash()`, or database schemas.
- No new external dependencies.

## 4. Acceptance Criteria
- [ ] Historical reasoning is preserved in `docs/maker-config-legacy-rationale.md` and ADR 0004 exists.
- [ ] No live Python code outside `strategy/config.py` imports `MakerConfig` or `strategy.config`.
- [ ] `strategy/markets.py:413-427` is removed (no broken scratch script).
- [ ] `tests/test_legacy_maker_config.py` asserts the boundary and passes.
- [ ] Targeted tests pass: `tests/test_live_trader.py`, `tests/test_param_registry.py`, `tests/test_backtest_engine.py`, `tests/test_docstrings.py`, `tests/test_markets_hosts.py`.
