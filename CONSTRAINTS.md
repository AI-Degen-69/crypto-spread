# CONSTRAINTS.md — Issue #147: run-folders layout (Stage 1)

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest -q` fully green; new gate `python -m pytest tests/test_run_layout.py -q` green.
- **No Test Swallowing**: no skipped assertions; kind-validation (`live`/`paper` only), naming-format, and manifest-schema keys covered by unit tests; pilot smoke test writes into a tmp dir via `--outdir`, never into real `runs/`.
- **Anti-Cheat**: no weakening of existing dashboard/collector assertions to fit the move; no deleting tests that reference old paths without replacing their coverage.

### 2. Data-Integrity & Cutover Boundaries
- **Hash-before-delete**: every migrated byte hash-verified (e.g. SHA-256) against the source before any source file is removed; abort the migration on first mismatch.
- **Hard cut, honest grep**: after migration, `run/shadow_ev` must not exist and no `*.py` code may reference it; remaining `run/shadow_ev` mentions in historical docs (research notes, handoff) are left untouched and listed in the PR body, not silently rewritten.
- **Gitignore**: `runs/` added to `.gitignore`; the migrated 11h example stays local (never `git add` data/HTML under `runs/`).
- **Scope honesty**: pilot results stub is labeled as auto-generated numbers + pointers (not the hand-authored showcase); conclusions paper stays manual post-analysis — no fabricated analysis text.

### 3. Perf & Dependencies
- **Perf**: migration is a file-copy + hash pass over ~7MB (snapshots 7.3MB); pilot runtime behavior unchanged (1s loop, 60s snapshot cadence).
- **Dependencies**: none new (stdlib + existing only). No quoting/pricing/risk logic touched; cockpit engine untouched.
