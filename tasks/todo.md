# Todo: Issue #137 — patient undecided-band maker preset

- [x] Task 1: Engine knobs — init + update_config + state echo (`strategy/live_trader.py`)
- [ ] Task 2: Entry-delay gate in the quote path (`strategy/live_trader.py`)
- [ ] Task 3: Post-delay entry-band gate, re-entry exempt (`strategy/live_trader.py`)
- [ ] Task 4: stop_loss_enabled gate on stop paths, timeout/settlement intact (`strategy/live_trader.py`)
- [ ] Task 5: Preset + API wiring — `POST /api/live/config`, `GET /api/live/state` (`server/osc_dash.py`)
- [ ] Task 6: New tests + regression gate (`python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py -q`, then `python -m pytest -q`)
