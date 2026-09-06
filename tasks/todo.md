# Task List - Control Center Menu Script (`csm`) & Telemetry Monitor (Issue #64)

## Phase 1: Script Core & Telemetry (`status`)
- [x] Task 1: Create `scripts/crypto-spread-menu.ps1` with Theme integration, ASCII fallback, parameter handling, and `status` action
  - Acceptance: Handles `status`, dot-sources theme files or falls back, queries port `:8802`, `/api/live/state`, `/api/collector/status`, reads `run/ticks/manifest.json`, and formats themed key-value table.
  - Verify: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 status"`
  - Files: `scripts/crypto-spread-menu.ps1`

## Phase 2: Process Control (`open` & `stop`)
- [x] Task 2: Implement `open` (background hosting) and PID safety tracking in `scripts/crypto-spread-menu.ps1`
  - Acceptance: `open` launches `uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802` in hidden window, records PID + start ticks in `run/dash.pids.json`, verifies `:8802`, provides adoption check if already running, and opens browser.
  - Verify: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 open"`
  - Files: `scripts/crypto-spread-menu.ps1`

- [x] Task 3: Implement `stop` (tree cleanup & orphan sweep) in `scripts/crypto-spread-menu.ps1`
  - Acceptance: `stop` tree-kills dashboard process (`taskkill /F /T`), verifies process exit, confirms `:8802` port release, and deletes `run/dash.pids.json`.
  - Verify: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 stop"`
  - Files: `scripts/crypto-spread-menu.ps1`

## Checkpoint 1: Core Process Control & Telemetry Operational
- [x] Status, open, and stop actions verified via `pwsh -NoProfile`.

## Phase 3: Live Price Stream Monitor & Terminal Wrapper (`compare` & `csm`)
- [x] Task 4: Implement `compare` action in `scripts/crypto-spread-menu.ps1`
  - Acceptance: `compare` streams live Binance spot vs. Polymarket CLOB book ticks to terminal.
  - Verify: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 compare --ticks 2"`
  - Files: `scripts/crypto-spread-menu.ps1`

- [x] Task 5: Register global `csm` & `crypto-spread-menu` functions in `C:\Program Files\PowerShell\7\profile.ps1`
  - Acceptance: Functions forward `@Args` directly to `scripts/crypto-spread-menu.ps1` with path existence check.
  - Verify: `pwsh -Command "csm status"`
  - Files: `C:\Program Files\PowerShell\7\profile.ps1`

## Phase 4: Automated Verification & Integration
- [x] Task 6: Add Pytest verification suite in `tests/test_crypto_spread_menu.py`
  - Acceptance: Tests PID registry JSON format, start ticks validation, and verifies full test suite passes.
  - Verify: `python -m pytest tests/test_crypto_spread_menu.py -q`
  - Files: `tests/test_crypto_spread_menu.py`

## Checkpoint 2: Complete Implementation & Final Verification
- [x] All 6 tasks completed.
- [x] `python -m pytest -q` passes all tests.
