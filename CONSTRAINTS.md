# CONSTRAINTS.md — Issue #123 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests stay green: `python -m pytest -q` full suite (409+ tests). Zero modifications to existing assertions without technical justification.
- Targeted gates: `tests/test_live_trader.py`, `tests/test_entry_timeout.py`.
- New tests (red → green) required for every new behavior:
  (a) single leg fill triggers chase quote on opposite leg up to the ask;
  (b) chase quote respects `max_pair_cost` cap (`first_leg_fill + chase_quote <= max_pair_cost`);
  (c) chase does not trigger when both legs are already filled;
  (d) reentry / completion telemetry distinguishes chased fills from passive fills;
  (e) new knobs round-trip through `update_config` with range validation.

## 2. Anti-Cheat & Integrity
- No disabling, skipping, weakening or deleting existing tests or assertions.
- Existing entry gates (late-start #96, adverse-open #92, entry-timeout, drift-skip #95) and exit/stop rules (#87, #124) stay fully functional.
- Chase logic only acts when exactly one leg is filled, never on zero fills or completed pairs.

## 3. Performance & Runtime Bounds
- Leg-chase calculation runs purely in memory on the per-tick path (sub-millisecond); no new threads, slow lookups, or network blocking.

## 4. Dependencies & Scope
- Zero new external libraries.
- Scope limited to post-fill leg-chase logic, config fields, telemetry, and unit tests.

## 5. Verification Status (checked Sep 11, 2026)
- Targeted gates: `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py -q` -> **158 passed**, zero failures.
- Full suite: `python -m pytest -q` -> **414 passed**, zero failures (100% green).

