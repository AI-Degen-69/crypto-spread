# Todo: Issue #151 — external collector detection + start guard

- [x] Task 1: Detection helper + status `source` field (TDD) (`python -m pytest tests/test_osc_dash_integration.py -q -k "external or collector"`)
- [x] Task 2: Start guard — 409 while external live (TDD) (`python -m pytest tests/test_osc_dash_integration.py -q -k "collector"`)
- [x] Task 3: Status matrix tests none/external-only/child-only (`python -m pytest tests/test_osc_dash_integration.py -q -k "collector"`)
- [x] Task 4: Banner + toggle UI truth — external state + 409 reason (grep + existing `collectorBadge` assertions green)
- [ ] Task 5 (OPTIONAL, needs approval): guard `poll-once` too (`python -m pytest tests/test_osc_dash_integration.py -q -k "poll_once"`)
- [x] Task 6: Full regression gate (`python -m pytest -q`, `git status --short`)
