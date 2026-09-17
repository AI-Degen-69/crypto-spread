# Plan — Issue #214: Engine Parity Test Harness

- **Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/214
- **Branch:** `feat/parity-harness-214`
- **Size tier:** **Standard** — one new test module (`tests/test_engine_parity.py`), doc updates (`AGENTS.md`).
- **Task type:** `Test infrastructure`
- **Stack:** Python 3.12, pytest. Targeted tests only locally; CI is the merge gate (`AGENTS.md`).
- **Spec:** `SPEC.md`. **Gates:** `CONSTRAINTS.md`. **Rules of record:** `docs/engine-decision-rules.md`.
- **Interview:** Requirements fully clear from issue and rules doc — interview-me skipped.

---

## Locked Interfaces (`tests/test_engine_parity.py`)

```python
def snaps_to_polls(snaps: list[dict]) -> list[tuple[float, dict]]:
    """Convert backtest snaps to (now, poll_data) tuples for LiveTraderEngine._update_market_strategy."""

def live_outcome(snaps: list[dict], params: BacktestParams) -> dict:
    """Run the live decision path headlessly in paper mode; return the SPEC §2 surface."""

def backtest_outcome(snaps: list[dict], params: BacktestParams) -> dict:
    """Run pure _simulate_window; return the identical SPEC §2 surface."""

def assert_parity(snaps: list[dict], params: BacktestParams) -> None:
    """Run both engines on snaps; raise AssertionError with human-readable diff if outcomes diverge."""
```

---

## Tasks

### [x] T0 — Branch + spec lock
Branch `feat/parity-harness-214` off `master`. `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md` written.

### [x] T1 — `[Test/Harness]` Snap-to-poll adapter (`snaps_to_polls`)
- **Files:** `tests/test_engine_parity.py`
- **Build:** `snaps_to_polls`. Translates synthetic tick dictionaries (containing `up_book`, `down_book`, `mid`, `start_ts`, `end_ts`, etc.) into `(now, poll_data)` consumable by `LiveTraderEngine._update_market_strategy`.
- **Skill:** `test-driven-development`.
- **Verify:** `python -m pytest tests/test_engine_parity.py -k test_adapter -q`.

### [x] T2 — `[Test/Harness]` Headless live engine execution (`live_outcome`)
- **Files:** `tests/test_engine_parity.py`
- **Build:** `live_outcome`. Initializes `LiveTraderEngine(load_persisted=False)` in `mode="paper"`, stubs network/background services (`stream_bridge.start`, `_schedule_wallet_balance_fetch`), maps `BacktestParams` to engine parameters, feeds each tick sequentially at its timestamp, and extracts the comparable surface.
- **Skill:** `test-driven-development`.
- **Verify:** `python -m pytest tests/test_engine_parity.py -k test_live_outcome -q`.

### [x] T3 — `[Test/Harness]` Backtest outcome & structured diff assertion (`assert_parity`)
- **Files:** `tests/test_engine_parity.py`
- **Build:** `backtest_outcome` (project `WindowResult` onto the identical surface keys) and `assert_parity`. Build human-readable error formatting showing the exact mismatched field, both values, and parameter context.
- **Skill:** `test-driven-development`.
- **Verify:** `python -m pytest tests/test_engine_parity.py -k test_assert_parity_diff -q`.

### [x] T4 — `[Test/Scenarios]` Seed scenarios for settled engine rules
- **Files:** `tests/test_engine_parity.py`
- **Build:** Comprehensive seed test suite:
  1. Balanced open to merged pair.
  2. Real mid anchor pricing (#206).
  3. Pair-cost cap capping chase without blocking quoting (#204).
  4. Unpriceable leg skipping entry (#207).
  5. Stop-loss anchored to fill price (#209 / #230).
  6. Multi-round fresh start outside dead zone (#232).
  7. Parameter variations matrix fixture (user-approved improvement).
- **Skill:** `test-driven-development`.
- **Verify:** `python -m pytest tests/test_engine_parity.py -q` (all tests pass in < 5s).

### [x] T5 — `[Docs]` Update repository documentation & gates
- **Files:** `AGENTS.md`
- **Build:** Reference `tests/test_engine_parity.py` as the canonical behavioral parity gate. Document that any future strategy decision change must add/update a scenario in the parity harness.
- **Verify:** `python -m pytest tests/test_docstrings.py -q`.

### [x] T6 — `[Review/Ship]` Verification and Station IV handoff
- **Build:** Run targeted test suites (`test_engine_parity.py`, `test_fresh_start_parity.py`, `test_live_trader.py`, `test_backtest_engine.py`).
- **Verify:** 100% pass, clean working directory.

---

## Commit Plan

1. `test(parity): build snap-to-poll adapter and headless live driver (#214)`
2. `test(parity): build backtest outcome extractor and readable assert_parity diff (#214)`
3. `test(parity): add comprehensive seed scenarios for engine rules (#214)`
4. `docs(agents): record test_engine_parity as the canonical parity gate (#214)`
