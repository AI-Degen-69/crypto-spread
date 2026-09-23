# Plan — Issue #313: single canonical dashboard port (5515)

Branch: i313/dashboard-single-canonical-port-constant-move-the | Issue: #313
Size: Standard (8 files across Python + PowerShell + docs, one architecture decision) · Type: Code + Docs
Stack: Python (FastAPI/uvicorn) + PowerShell 7 launchers · Tests: pytest (targeted only)

## Resolved answers (from code, recorded so the reasoning survives)
- Q1 single-source + PS read → (a) leaf `server/ports.py` + one python probe at
  menu startup (PYTHONPATH already set at `crypto-spread-menu.ps1:38`); loud fail
  if probe fails; parity test as backstop.
- Q2 override → none; plain constant; isolated `8888` passes `--port` to uvicorn directly.
- Q3 allow-list → replace `8802` with constant; keep `8888`/`8000`/`80`/`443` + runtime add.

## Dependency graph
- T1 (leaf + server import) unblocks T2 (observer), T3 (launcher), T5 (tests).
- T3 unblocks T5 (status-stdout assertion needs new port live-text).
- T4 docs is independent; runs any time after T1 value is fixed.
- T6 verification needs T1–T5 done.

## Tasks

### T1 [Code] Create `server/ports.py` + wire `server/osc_dash.py` — S
- New leaf: `DASHBOARD_HOST = "127.0.0.1"`, `DASHBOARD_PORT = 5515`,
  `DASHBOARD_URL`; zero imports from repo (stdlib only).
- `osc_dash.py`: docstring `:8802` → reference constant; `allowed_ports`
  `8802` → `DASHBOARD_PORT`; keep `8888`/`8000`/`80`/`443` + runtime add.
- Helper skill: test-driven-development. Depends on: none.
- Verify: `python -m pytest tests/test_osc_dash_integration.py -q -k origin`
  + `python -c "from server.ports import DASHBOARD_PORT; print(DASHBOARD_PORT)"` → 5515.

### T2 [Code] Point `scripts/observe_paper.py` at the constant — XS
- `BASE_URL` built from `server.ports` (`DASHBOARD_URL`); docstring `:8802`
  updated; `--url` override untouched.
- Helper skill: test-driven-development. Depends on: T1.
- Verify: `python -c "import scripts.observe_paper"` + grep shows no `8802` in file.

### T3 [Code] Launcher reads the probe once — M
- `scripts/crypto-spread-menu.ps1:28`: `$Port` resolved via
  `python -c "from server.ports import DASHBOARD_PORT..."` (uses existing
  PYTHONPATH), loud fail on probe error; update comments/UI strings
  (`:8,:134,:139,:191,:202,:223,:314,:317,:499,:500`).
- Helper skill: incremental-implementation. Depends on: T1.
- Verify: `pwsh -NoProfile scripts/crypto-spread-menu.ps1 status` shows 5515.

### CHECKPOINT 1 — server + observer + launcher agree on 5515; status text shows new port.

### T4 [Docs] Living docs to 5515 — S
- `AGENTS.md:32,:49,:62,:79`, `README.md:7,:20,:27`,
  `docs/operations.md:17,:21,:65-:66` → 5515. Frozen `docs/issues/*.html` untouched.
- Helper skill: documentation-and-adrs. Depends on: T1 (value fixed).
- Verify: grep finds `8802` only under `docs/issues/` + gitignored.

### T5 [Code] Tests: parity + no-literal regression — S
- `tests/test_crypto_spread_menu.py`: `8802` literals → `5515`/constant import;
  PID fixture port → 5515; new test asserts `DASHBOARD_PORT == 5515` and scans
  `server/`, `scripts/` (tracked `.ps1`/`.py`), `tests/`, living docs with
  word-boundary port pattern (so `0.008802...` research floats never false-fail).
- Helper skill: test-driven-development. Depends on: T1, T3.
- Verify: `python -m pytest tests/test_dashboard_ports.py tests/test_crypto_spread_menu.py -q`.

### CHECKPOINT 2 — docs + tests green; only frozen HTML still mentions 8802.

### T6 [Code] Final sweep: repo-wide 8802 audit — XS
- Search in-scope paths for port literals; confirm remaining hits are only
  `docs/issues/*.html`, gitignored runtime, or non-port numbers; run both
  targeted suites once more.
- Helper skill: incremental-implementation. Depends on: T1–T5.
- Verify: `python -m pytest tests/test_dashboard_ports.py tests/test_crypto_spread_menu.py tests/test_osc_dash_integration.py -q -k "menu or origin or port"`.

## Improvement proposal (adopted by default)
- Regression scan uses word-boundary port matching, not naive `8802` substring —
  evidence verbatim: `research/sweeps/phase1_1d.json:6615`
  `"pair_rate": 0.008802816901408451`. Prevents research floats from false-failing the gate.

## Rejected scope
- None beyond the issue's own out-of-scope list (frozen HTML, gitignored runtime,
  env/CLI override, glossary, i312 branch).

## Files
tasks/plan.md · tasks/todo.md · CONSTRAINTS.md · SPEC.md
