# Plan: Issue #147 — run-folders layout (Stage 1, no entrypoint unification)

Task Type: Code + Docs
Size Tier: Standard
Target Files: scripts/run_layout.py (new), scripts/shadow_ev_pilot.py, tests/test_run_layout.py (new),
  docs/run-conventions.md (new), .gitignore, AGENTS.md, README.md, docs/operations.md,
  docs/issue-workflow.md, runs/paper/2026-09-11_22-10_IDT/ (local, gitignored)

Decisions locked with user: `runs/` gitignored (migration is local-only) · hard cut, no shim ·
  papers derived from HTML contents (explained→abstract, showcase→results, showcase tail→conclusions).

## Task Breakdown

### Task 1: Layout helper (naming, manifest schema, paper stubs)
- **Files**: `scripts/run_layout.py` (new)
- **Type**: Code
- **Description**:
  1. `RUNS_ROOT`, `new_run_dir(kind, start, tz_abbr)` creating `runs/{paper|live}/YYYY-MM-DD_HH-MM_TZ/` + `data/` + `research-papers/`; `ValueError` on non-paper/live kind.
  2. `write_manifest` (§6 schema), `write_summary_html`, `write_paper_stub` (themed shell).
  3. TZ helper: local abbr via `datetime.now().astimezone().tzname()`.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_run_layout.py -q -k "naming or kind or schema"`

### Task 2: Layout unit tests + pilot smoke scaffolding
- **Files**: `tests/test_run_layout.py` (new)
- **Type**: Code
- **Description**:
  1. Naming format (`YYYY-MM-DD_HH-MM_TZ`, no colons, sortable), kind validation, manifest schema keys, stub/summary writers round-trip in `tmp_path`.
  2. Pilot smoke test into tmp dir via `--outdir` asserting `data/` + `manifest.json` + `summary.html` + abstract stub exist.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_run_layout.py -q`

### Task 3: Pilot writes the new layout directly
- **Files**: `scripts/shadow_ev_pilot.py`
- **Type**: Code
- **Description**:
  1. `RUN_DIR` → `run_layout.new_run_dir("paper", …)`; `meta/snapshots/trades/final` → `data/`; keep `--outdir` writing the same layout inside the given dir.
  2. Abstract stub at start (preset + universe + config hypothesis), results stub at stop (final numbers table + data pointers), manifest + summary at stop; fix `:3` docstring ref to the canonical papers path.
  3. No quoting/pricing/risk change; paper-mode guards untouched.
- **Status**: [x]
- **Verification**: `python -m scripts.shadow_ev_pilot --hours 0.01 --snap-every 5 --outdir <tmp>` then `python -m pytest tests/test_run_layout.py -q`

### Task 4: Migrate the 11h run (hash-verified, hard cut)
- **Files**: `runs/paper/2026-09-11_22-10_IDT/` (new, local); deletes under `run/shadow_ev/`, `docs/reports/`
- **Type**: Code
- **Description**:
  1. Copy 4 JSONs → `data/` + log tail; SHA-256 verify before any delete.
  2. Papers per SPEC §4 (explained→abstract + banner, showcase→results with stale-ref fixes, new conclusions from showcase tail); generate `summary.html` + `manifest.json` (final: +6.74 realized / 66 trades / 53 pairs / 97% win / 0 stops).
  3. Delete `run/shadow_ev_20260911_220912` (aborted), `…_221054`, empty parent, the two `docs/reports/` HTMLs. `.freebuff/` untouched.
- **Status**: [x]
- **Verification**: hashes match; `Test-Path run/shadow_ev` is False; folder tree matches the convention

### Task 5: Convention doc + ref updates
- **Files**: `docs/run-conventions.md` (new), `.gitignore`, `AGENTS.md`, `README.md`, `docs/operations.md`, `docs/issue-workflow.md`
- **Type**: Docs
- **Description**:
  1. `docs/run-conventions.md` = SPEC §2 + taxonomy (run papers vs issue showcases vs scratch) + lifecycle (abstract@start, results@stop, conclusions post-run).
  2. `.gitignore` +`runs/`; AGENTS.md structure (`runs/` line, `run/` line stays); README/operations one-line pointers.
  3. `docs/issue-workflow.md`: Station 4 output → `docs/issues/<id>-<kind>-<slug>.html` (kind ∈ {showcase, explained}, slug from issue title); §6 prune paths updated to the same scheme; artifact-home rule subsection (repo finding: no doc/skill ever mandated `.freebuff/` — it grew organically as gitignored scratch, so this writes down the real rule).
  4. Global skill `~/.agents/skills/explain-issue/SKILL.md` (+ `reference.md:15`): same `docs/issues/<id>-<kind>-<slug>.html` scheme for the persisted artifact (`$TEMP` stays preview-only). Follow-up (not this issue): global `prune-artifacts` skill still matches `docs/reports/issue_*` — needs its own update once new-scheme files exist.
  3. `server/osc_dash.py`: verify-only (grep shows no `shadow_ev`/`reports` refs) — no logic change.
- **Status**: [x]
- **Verification**: `rg -n "run/shadow_ev" --glob '*.py'` empty; `python -m pytest -q` green

### Task 6: Full regression gate + commit
- **Files**: —
- **Type**: Code
- **Description**:
  1. `python -m pytest -q` (0 failures); confirm `git status` shows no data files under `runs/` staged.
  2. Commit on a `feat/147-…` branch per `docs/git-workflow.md` (gan-harness removal rides along as its own commit — owner confirmed it served its purpose).
- **Status**: [x]
- **Verification**: `python -m pytest -q` + `git status --short`
