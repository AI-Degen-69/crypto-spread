# SPEC: Control Center Menu Script (csm) & Telemetry Monitor

## Objective
Add a standalone PowerShell 7 control menu (`scripts/crypto-spread-menu.ps1`) for `crypto-spread` alongside terminal aliases (`csm`, `crypto-spread-menu`) in `C:\Program Files\PowerShell\7\profile.ps1`. The menu provides background dashboard hosting on port `:8802`, PID tracking with recycling protection, clean process tree termination, system telemetry status display, and real-time streaming of Binance spot vs. Polymarket CLOB order book tick prices.

## Tech Stack
- PowerShell 7 (`pwsh`), UTF-8 encoding without BOM.
- Python 3.10+ (`uvicorn`, `fastapi`, `requests`, `websockets`).
- `Theme-ColorSystem.ps1` and `Theme-Templates.ps1` from `C:\Program Files\PowerShell\7\scripts\Theme\`.
- Windows process & network utilities (`taskkill`, `netstat`).

## Commands
- **Interactive Menu**: `.\scripts\crypto-spread-menu.ps1` or `csm`
- **CLI Direct Dispatch**:
  - `csm status` / `.\scripts\crypto-spread-menu.ps1 status` — Displays system telemetry, dashboard state, bot status, and tick store manifest.
  - `csm open` / `.\scripts\crypto-spread-menu.ps1 open` — Starts uvicorn on `:8802` detached, registers PID + ticks, and opens browser.
  - `csm stop` / `.\scripts\crypto-spread-menu.ps1 stop` — Terminates dashboard process tree, frees `:8802`, and cleans PID registry.
  - `csm compare` / `.\scripts\crypto-spread-menu.ps1 compare` — Terminal live price monitor comparing Binance spot vs CLOB order book ticks.
- **Verification**: `python -m pytest -q`

## Project Structure
```
c:\Users\Tiger\Agents\Projects\AI Trading\crypto-spread\
├── scripts/
│   └── crypto-spread-menu.ps1  [NEW] Control center script
├── run/
│   └── dash.pids.json          PID and tick registry for dashboard process
├── tests/
│   └── test_crypto_spread_menu.py [NEW] Verification tests for menu helper endpoints/CLI behavior
└── C:\Program Files\PowerShell\7\profile.ps1 [MODIFY] Register csm & crypto-spread-menu aliases
```

## Code Style & Principles
- **PowerShell 7 Native**: `[CmdletBinding()]`, UTF-8, strict handling of process objects and network ports.
- **Theme-First Formatting**: Dot-source `Theme-ColorSystem.ps1` and `Theme-Templates.ps1`. Render banners via `Write-ProfileBanner`/`Write-ProfileSection`, status lines via `Write-ProfileSuccess`/`Warning`/`Error`/`Info`, and key-value tables via `Write-ProfileKeyValue`.
- **Graceful Fallbacks**: Include inline ASCII fallback implementations for all `Write-Profile*` functions if Theme files are missing or host is non-interactive.
- **PID Safety**: Always check `.StartTime.ToUniversalTime().Ticks` when evaluating PID ownership from `run/dash.pids.json` to prevent killing recycled PIDs.

## Testing Strategy
- Run unit test suite: `python -m pytest -q`
- Validate `csm` execution from PowerShell 7 shell across direct dispatches (`status`, `open`, `stop`, `compare`).
- Test process tree termination and orphan adoption logic under test scenarios.

## Boundaries
- **Always do**: Validate `:8802` status before binding or killing; check PID creation ticks before killing recorded process IDs; support CLI parameters without prompting.
- **Ask first**: Editing existing unrelated profile functions in `profile.ps1`.
- **Never do**: Kill un-owned third-party processes on port `:8802`; remove `run/` from `.gitignore`.

## Success Criteria
1. `csm` executes `scripts/crypto-spread-menu.ps1` from any working directory in PowerShell 7.
2. `csm status`, `csm open`, `csm stop`, `csm compare` dispatch immediately without opening interactive prompts.
3. Native `Theme-ColorSystem.ps1` & `Theme-Templates.ps1` integration renders themed banners, sections, tables, and status badges.
4. `open` action launches `uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802` in background, records PID & start ticks in `run/dash.pids.json`, verifies `:8802`, and opens `http://127.0.0.1:8802`.
5. `stop` action tree-kills dashboard process (`taskkill /F /T`), verifies `:8802` release, and sweeps stale PID file.
6. `status` action queries `/api/live/state`, `/api/collector/status`, `:8802` listener, and `run/ticks/manifest.json`.
7. `compare` action streams real-time Binance spot vs. Polymarket CLOB book ticks in terminal.
8. All existing 186 unit tests pass.

## Open Questions
- None. Requirements and architecture are fully locked in.
