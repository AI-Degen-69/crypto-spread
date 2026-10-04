# Task Plan — Issue #426: Retire strategy/config.py MakerConfig as executable surface

Branch: i426/chore-strategy-strategy-config-py-makerconfig | Issue: #426

## CodeRabbit Plan Intake
- **Adopted**: Preserving measured rationale into a dedicated markdown document (`docs/maker-config-legacy-rationale.md`), recording the architectural decision in ADR 0004 (`docs/adr/0004-retire-maker-config.md`), stripping `strategy/config.py` to a clear legacy reference stub with explicit docstrings and deprecation markers, removing the broken `load()` and the broken `if __name__ == '__main__':` block in `strategy/markets.py`, adding source-guard tests in `tests/test_legacy_maker_config.py`, updating references in `AGENTS.md` and docs.
- **Rejected**: Multi-phase PR splitting across several PRs; all changes are coherent and belong to this single issue.
- **Verified Code Facts**: Verified that `MakerConfig` declares 94 fields, lacks the fields injected by `load()`, has no live consumers across the codebase, and `strategy/markets.py:413-427` raises `AttributeError` if run.

## Improvement Proposal (Evidence-based)
- **Motivating Evidence**: `strategy/config.py:649` (`max_pair_cost: float = 0.995`) was cited in issue #421 as a live default, whereas `strategy/live_trader.py:920` declares `self.max_pair_cost: float = 0.99` ("this is what an unconfigured engine starts at").
- **Proposal**: Add a dedicated assertion in `tests/test_legacy_maker_config.py` verifying that `LiveTraderEngine().max_pair_cost == 0.99` and that no module in `strategy/` exports `0.995` as a default pair cost.

## Tasks

### Task 1: Preserve historical rationale and record ADR 0004 [Docs] [x]
- **Size**: S
- **Domain**: `[Docs]`
- **Target Files**: `docs/maker-config-legacy-rationale.md`, `docs/adr/0004-retire-maker-config.md`
- **Depends on**: None
- **Description**:
  1. Extract and organize the ~600 lines of historical hunter-fleet measured rationale from `strategy/config.py` into `docs/maker-config-legacy-rationale.md`, explicitly flagging that these are historical notes from a retired experiment and pointing to `LiveTraderEngine.__init__` and `BacktestParams` as real configuration homes.
  2. Create ADR 0004 (`docs/adr/0004-retire-maker-config.md`) using `docs/adr/template.md` documenting the decision to retire `MakerConfig` and `load()`.
- **Verification**: Verify files exist, are formatted cleanly, and contain the required cross-references.

### Task 2: Implement legacy MakerConfig import & boundary guard tests [Backend/Logic] [x]
- **Size**: S
- **Domain**: `[Backend/Logic]`
- **Target Files**: `tests/test_legacy_maker_config.py`
- **Depends on**: Task 1
- **Description**:
  1. Create `tests/test_legacy_maker_config.py` with tests asserting:
     - No `.py` file outside `strategy/config.py` imports `MakerConfig` or `strategy.config`.
     - `strategy.config` does not expose `load()`.
     - `strategy/config.py` module and `MakerConfig` class docstrings clearly state they are legacy / non-runtime and name `LiveTraderEngine.__init__` and `BacktestParams` as configuration owners.
     - `LiveTraderEngine().max_pair_cost == 0.99`, preventing confusion with historical `0.995`.
- **Verification**: `python -m pytest tests/test_legacy_maker_config.py -q` (initially fails on unstripped module, then passes after Task 3).

### Task 3: Strip strategy/config.py and remove broken markets.py __main__ block [Backend/Logic] [x]
- **Size**: S
- **Domain**: `[Backend/Logic]`
- **Target Files**: `strategy/config.py`, `strategy/markets.py`
- **Depends on**: Task 2
- **Description**:
  1. Strip `strategy/config.py`:
     - Remove `load()`.
     - Remove dead `# hook probe` lines.
     - Remove obsolete methods (`db_path()`, `effective_naked_cap()`, etc.).
     - Retain stripped dataclass definition with explicit deprecation docstrings and pointer to `docs/maker-config-legacy-rationale.md`.
  2. Remove `if __name__ == "__main__":` scratch block in `strategy/markets.py:413-427`.
- **Verification**: `python -m pytest tests/test_legacy_maker_config.py tests/test_docstrings.py tests/test_markets_hosts.py -q`.

### Task 4: Update repo documentation and run targeted regression suites [Docs & Verification] [x]
- **Size**: S
- **Domain**: `[Docs]`
- **Target Files**: `AGENTS.md`, `docs/operations.md`, `docs/research-spread-bot-conclusions.md`
- **Depends on**: Task 3
- **Description**:
  1. Update `AGENTS.md:50` to state `strategy/config.py` is retired legacy, pointing to ADR 0004 and `docs/maker-config-legacy-rationale.md`.
  2. Update citations in `docs/operations.md` and `docs/research-spread-bot-conclusions.md`.
  3. Run targeted verification suite.
- **Verification**:
  `python -m pytest tests/test_live_trader.py tests/test_param_registry.py tests/test_backtest_engine.py tests/test_docstrings.py tests/test_markets_hosts.py tests/test_legacy_maker_config.py -q`
