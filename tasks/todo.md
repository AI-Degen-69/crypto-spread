# Checklist — Issue #426

- [x] Task 1: Preserve historical rationale in `docs/maker-config-legacy-rationale.md` and record ADR 0004
- [x] Task 2: Implement legacy MakerConfig import & boundary guard tests in `tests/test_legacy_maker_config.py`
- [x] Task 3: Strip `strategy/config.py` (remove `load()`, `# hook probe`, dead methods, add legacy banner) and remove broken `strategy/markets.py` `__main__` block
- [x] Task 4: Update repo documentation (`AGENTS.md`, `docs/`) and verify targeted test suites
