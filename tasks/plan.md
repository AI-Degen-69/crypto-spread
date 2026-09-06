# Implementation Plan: Control Center Menu Script (`csm`) & Telemetry Monitor (Issue #64)

## Overview
Add a standalone PowerShell 7 control menu (`scripts/crypto-spread-menu.ps1`) for `crypto-spread` alongside terminal aliases (`csm`, `crypto-spread-menu`) in `C:\Program Files\PowerShell\7\profile.ps1`. The menu provides background dashboard hosting on port `:8802`, PID tracking with recycling protection (`run/dash.pids.json`), clean process tree termination (`taskkill`), system telemetry status display (`/api/live/state`, `/api/collector/status`, `manifest.json`), and real-time streaming of Binance spot vs. Polymarket CLOB order book tick prices.

## Architecture Decisions
1. **Native Profile Theme System Integration**: Dot-source `Theme-ColorSystem.ps1` and `Theme-Templates.ps1` from `C:\Program Files\PowerShell\7\scripts\Theme\`. Include self-contained fallback functions (`Write-ProfileSuccess`, `Write-ProfileWarning`, `Write-ProfileError`, `Write-ProfileInfo`, `Write-ProfileBanner`, `Write-ProfileKeyValue`) so the menu functions seamlessly even if theme scripts are missing.
2. **PID Recycling Protection**: Record process start ticks (`.StartTime.ToUniversalTime().Ticks`) into `run/dash.pids.json` on launch. Validate start ticks prior to terminating processes to ensure recycled process IDs are never accidentally killed.
3. **Clean Tree Termination**: Use `taskkill /F /T /PID $pid` followed by process exit verification (`Get-Process -Id $pid`) and netstat listener sweep to ensure port `:8802` is completely freed.
4. **Zero-Interaction CLI Dispatch**: Support direct CLI action dispatching (`csm status`, `csm open`, `csm stop`, `csm compare`) without prompting or waiting for menu keypresses.
5. **Global Terminal Access (`csm`)**: Register `function csm` and `function crypto-spread-menu` in `C:\Program Files\PowerShell\7\profile.ps1` with script path existence checks before execution.

## Task List

### Phase 1: Script Core & Telemetry (`status`)
- [x] Task 1: Create `scripts/crypto-spread-menu.ps1` with Theme integration, ASCII fallback, parameter handling, and `status` action
  - **Acceptance**: Script parses positional parameter (`status`, `open`, `stop`, `compare`), dot-sources theme scripts or falls back, queries port `:8802`, `/api/live/state`, `/api/collector/status`, and reads `run/ticks/manifest.json`.
  - **Verify**: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 status"`
  - **Files**: `scripts/crypto-spread-menu.ps1`

### Phase 2: Process Control (`open` & `stop`)
- [ ] Task 2: Implement `open` (background hosting) and PID safety tracking in `scripts/crypto-spread-menu.ps1`
  - **Acceptance**: `open` launches `uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802` in hidden window, records PID + start ticks in `run/dash.pids.json`, verifies `:8802`, provides adoption check if already running, and opens browser.
  - **Verify**: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 open"`
  - **Files**: `scripts/crypto-spread-menu.ps1`

- [ ] Task 3: Implement `stop` (tree cleanup & orphan sweep) in `scripts/crypto-spread-menu.ps1`
  - **Acceptance**: `stop` tree-kills dashboard process (`taskkill /F /T`), verifies process exit, confirms `:8802` port release, and deletes `run/dash.pids.json`.
  - **Verify**: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 stop"`
  - **Files**: `scripts/crypto-spread-menu.ps1`

### Checkpoint 1: Core Process Control & Telemetry Operational
- [ ] `status` action renders formatted system dashboard.
- [ ] `open` action starts dashboard in background, writes PID registry, and opens browser.
- [ ] `stop` action cleanly kills process tree and frees port `:8802`.

### Phase 3: Live Price Stream Monitor & Terminal Wrapper (`compare` & `csm`)
- [ ] Task 4: Implement `compare` action in `scripts/crypto-spread-menu.ps1`
  - **Acceptance**: `compare` streams live Binance spot vs. Polymarket CLOB book ticks to terminal.
  - **Verify**: `pwsh -NoProfile -Command ".\scripts\crypto-spread-menu.ps1 compare --ticks 2"`
  - **Files**: `scripts/crypto-spread-menu.ps1`

- [ ] Task 5: Register global `csm` & `crypto-spread-menu` functions in `C:\Program Files\PowerShell\7\profile.ps1`
  - **Acceptance**: Functions forward `@Args` directly to `scripts/crypto-spread-menu.ps1` with path existence check.
  - **Verify**: `pwsh -Command "csm status"`
  - **Files**: `C:\Program Files\PowerShell\7\profile.ps1`

### Phase 4: Automated Verification & Integration
- [ ] Task 6: Add Pytest verification suite in `tests/test_crypto_spread_menu.py`
  - **Acceptance**: Tests PID registry JSON format, start ticks validation, and verifies full test suite passes.
  - **Verify**: `python -m pytest tests/test_crypto_spread_menu.py -q`
  - **Files**: `tests/test_crypto_spread_menu.py`

### Checkpoint 2: Complete Implementation & Final Verification
- [ ] Interactive menu `[1]`, `[2]`, `[3]`, `[4]`, `[q]` and direct CLI parameters (`status`, `open`, `stop`, `compare`) execute cleanly.
- [ ] Global `csm` alias works from any directory.
- [ ] All unit tests pass: `python -m pytest -q`.

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Process ID recycled by Windows OS | High | Record `started_ticks` (`.StartTime.ToUniversalTime().Ticks`) in `run/dash.pids.json` and verify before calling `taskkill`. |
| Port 8802 occupied by unrelated process | Medium | Verify instance identity before attempting adoption or termination; warn user if port is occupied by foreign process. |
| Profile theme files absent or corrupted | Low | Self-contained fallback implementations defined in `crypto-spread-menu.ps1`. |

## Open Questions
- None. Requirements and architecture are fully locked in.
