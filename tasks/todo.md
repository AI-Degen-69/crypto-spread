# Todo: Issue #138 — per-fill queue-position telemetry

- [x] Task 1: Rest context + queue-ahead math + book stash (`strategy/live_trader.py`)
- [x] Task 2: Telemetry record builder + best-effort writer + tape join (`strategy/live_trader.py`)
- [ ] Task 3: Hook CLOB-confirmed + paper-simulated fill paths (`strategy/live_trader.py`)
- [ ] Task 4: Hook stream-detected fill path (`strategy/live_trader.py`)
- [ ] Task 5: Bucketing helper script (`scripts/bucket_fills.py`)
- [ ] Task 6: New tests + regression gate (`python -m pytest tests/test_live_trader.py -q`, then `python -m pytest -q`)
