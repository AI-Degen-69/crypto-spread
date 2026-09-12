# Todo: Issue #147 — run-folders layout (Stage 1)

- [x] Task 1: Layout helper `scripts/run_layout.py` — naming, manifest schema, paper stubs (`python -m pytest tests/test_run_layout.py -q -k "naming or kind or schema"`)
- [x] Task 2: `tests/test_run_layout.py` — unit tests + pilot smoke scaffolding (`python -m pytest tests/test_run_layout.py -q`)
- [x] Task 3: Pilot writes new layout directly (`scripts/shadow_ev_pilot.py`; smoke: `python -m scripts.shadow_ev_pilot --hours 0.01 --snap-every 5 --outdir <tmp>`)
- [x] Task 4: Migrate 11h run hash-verified into `runs/paper/2026-09-11_22-10_IDT/`; hard-cut deletes (verify hashes + `Test-Path run/shadow_ev` is False)
- [x] Task 5: `docs/run-conventions.md` + ref updates (.gitignore, AGENTS.md, README, operations, issue-workflow artifact-home rule); osc_dash verify-only (`python -m pytest -q`)
- [x] Task 6: Full regression gate + commit on `feat/147-…` (`python -m pytest -q`, `git status --short`)
