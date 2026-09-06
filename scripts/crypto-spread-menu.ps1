# CRYPTO SPREAD - CONTROL CENTER
# Standalone menu for crypto-spread 5m/15m SPREAD-2 capture engine
# (c:\Users\Tiger\Agents\Projects\AI Trading\crypto-spread).
#
# Usage:
#   .\scripts\crypto-spread-menu.ps1          # interactive menu
#   .\scripts\crypto-spread-menu.ps1 status   # [1] system status telemetry
#   .\scripts\crypto-spread-menu.ps1 open     # [2] host & open dashboard (background)
#   .\scripts\crypto-spread-menu.ps1 stop     # [3] stop dashboard & clean up processes
#   .\scripts\crypto-spread-menu.ps1 compare  # [4] live Binance spot vs CLOB book monitor
#

#Requires -Version 7.0

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = "",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Port        = 8802
$DashUrl     = "http://127.0.0.1:$Port"
$RunDir      = Join-Path $ProjectPath "run"
$TicksDir    = Join-Path $RunDir "ticks"
$DashPidFile = Join-Path $RunDir "dash.pids.json"
$OutLog      = Join-Path $RunDir "dash.out.log"
$ErrLog      = Join-Path $RunDir "dash.err.log"

# Ensure runtime directory exists
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

# ── Theme system (shared profile templates, self-contained fallback) ──
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding           = [System.Text.Encoding]::UTF8
} catch {}

$ThemeColorPath = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-ColorSystem.ps1"
$ThemeTplPath   = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-Templates.ps1"

if ((Test-Path $ThemeColorPath) -and (Test-Path $ThemeTplPath)) {
    try { . $ThemeColorPath; . $ThemeTplPath } catch {}
}

if (-not (Get-Command Write-ProfileSuccess -ErrorAction SilentlyContinue)) {
    function Get-ProfileColor {
        param([string]$Name)
        switch ($Name) {
            "Success"   { [ConsoleColor]::Green }
            "Error"     { [ConsoleColor]::Red }
            "Warning"   { [ConsoleColor]::Yellow }
            "Info"      { [ConsoleColor]::Cyan }
            "Neutral"   { [ConsoleColor]::DarkGray }
            "Strong"    { [ConsoleColor]::White }
            "Highlight" { [ConsoleColor]::Yellow }
            "Border"    { [ConsoleColor]::DarkCyan }
            "Path"      { [ConsoleColor]::Green }
            "Link"      { [ConsoleColor]::Blue }
            "Command"   { [ConsoleColor]::Cyan }
            "Argument"  { [ConsoleColor]::DarkYellow }
            "Danger"    { [ConsoleColor]::DarkRed }
            "Value"     { [ConsoleColor]::DarkCyan }
            "Progress"  { [ConsoleColor]::DarkGray }
            "Text"      { [ConsoleColor]::Gray }
            default     { [ConsoleColor]::Gray }
        }
    }
    function Write-ProfileBanner {
        param([string]$Title, [string]$Subtitle = "", [string]$Style = "Info")
        Write-Host ("=" * 80) -ForegroundColor (Get-ProfileColor -Name Border)
        Write-Host $Title -ForegroundColor (Get-ProfileColor -Name Info)
        if ($Subtitle) { Write-Host $Subtitle -ForegroundColor (Get-ProfileColor -Name Neutral) }
        Write-Host ("=" * 80) -ForegroundColor (Get-ProfileColor -Name Border)
        Write-Host ""
    }
    function Write-ProfileSection {
        param([string]$Title, [string]$Style = "Info")
        Write-Host ("─── $Title " + ("─" * [Math]::Max(5, (75 - $Title.Length)))) -ForegroundColor (Get-ProfileColor -Name Border)
    }
    function Write-ProfileSuccess { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [OK] " + $t) -ForegroundColor (Get-ProfileColor -Name Success) }
    function Write-ProfileError   { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [FAIL] " + $t) -ForegroundColor (Get-ProfileColor -Name Error) }
    function Write-ProfileWarning { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [WARN] " + $t) -ForegroundColor (Get-ProfileColor -Name Warning) }
    function Write-ProfileInfo    { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [INFO] " + $t) -ForegroundColor (Get-ProfileColor -Name Info) }
    function Write-ProfileNeutral { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [...] " + $t) -ForegroundColor (Get-ProfileColor -Name Neutral) }
    function Write-ProfileKeyValue {
        param([string]$Key, [string]$Value, [string]$Style = "Info", [int]$KeyWidth = 20)
        $k = ("{0,-$KeyWidth}" -f $Key)
        Write-Host "  $k" -ForegroundColor (Get-ProfileColor -Name Neutral) -NoNewline
        Write-Host " : $Value" -ForegroundColor (Get-ProfileColor -Name $Style)
    }
}

# ── Process & Network Primitives ──
function Test-Port {
    <# True when port 8802 is in LISTENING state. #>
    return [bool](netstat -ano | Select-String ":$Port\s+.*LISTENING")
}

function Get-PortPid {
    <# PID of process LISTENING on port 8802, or $null. #>
    $line = netstat -ano | Select-String ":$Port\s+.*LISTENING" | Select-Object -First 1
    if (-not $line) { return $null }
    return [int](($line.ToString() -split "\s+")[-1])
}

function Format-Uptime {
    param($Start)
    if (-not $Start) { return "" }
    $el = (Get-Date) - $Start
    if ($el.TotalHours -ge 1) { return ("{0}h {1}m" -f [int]$el.TotalHours, $el.Minutes) }
    if ($el.TotalMinutes -ge 1) { return ("{0}m {1}s" -f [int]$el.TotalMinutes, $el.Seconds) }
    return ("{0}s" -f [int]$el.TotalSeconds)
}

function Get-DashInstance {
    <# Recorded dashboard process that is STILL active (start-ticks checked). #>
    if (-not (Test-Path $DashPidFile)) { return $null }
    try { $data = Get-Content $DashPidFile -Raw | ConvertFrom-Json } catch { return $null }
    $d = $data.dash
    if (-not $d -or -not $d.pid) { return $null }
    try { $p = Get-Process -Id $d.pid -ErrorAction Stop } catch { return $null }
    if ($null -ne $d.started_ticks) {
        $pStart = $null
        try { $pStart = $p.StartTime } catch {}
        if ($null -eq $pStart -or $pStart.ToUniversalTime().Ticks -ne [int64]$d.started_ticks) {
            return $null # PID recycled; no longer our process
        }
    }
    return [pscustomobject]@{ pid = $p.Id; proc = $p; port = $Port }
}

function Save-DashInstance {
    param([Parameter(Mandatory)]$DashProcess)
    $record = $null
    if ($DashProcess -and -not $DashProcess.HasExited) {
        $record = [pscustomobject]@{
            pid           = $DashProcess.Id
            started_ticks = $DashProcess.StartTime.ToUniversalTime().Ticks
            started       = $DashProcess.StartTime.ToString("o")
            port          = $Port
        }
    }
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    [pscustomobject]@{
        strategy = "crypto-spread"
        saved    = (Get-Date).ToString("o")
        dash     = $record
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $DashPidFile -Encoding UTF8
}

function Test-DashboardServer {
    <# True when port 8802 answers as crypto-spread dashboard #>
    try {
        $r = Invoke-RestMethod -Uri "$DashUrl/api/oscillation" -UseBasicParsing -TimeoutSec 3
        return ($null -ne $r -and $null -ne $r.summary)
    } catch {
        return $false
    }
}

# ── Status Action ──
function Show-SystemStatus {
    Write-ProfileBanner -Title "CRYPTO SPREAD — TELEMETRY & SYSTEM STATUS" -Subtitle "5m/15m BTC/ETH/BNB/SOL/XRP Spread Capture Lab"
    
    # 1. Dashboard Process & Port Status
    Write-ProfileSection -Title "Dashboard Server (:8802)"
    $isListening = Test-Port
    $inst = Get-DashInstance
    $portPid = Get-PortPid

    if ($inst) {
        Write-ProfileSuccess -Message "Dashboard Server" -Detail "RUNNING (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))"
        Write-ProfileKeyValue -Key "URL" -Value $DashUrl -Style "Link"
        Write-ProfileKeyValue -Key "PID Registry" -Value $DashPidFile -Style "Path"
    } elseif ($isListening) {
        Write-ProfileWarning -Message "Dashboard Server" -Detail "LISTENING (PID $portPid, unowned by menu registry)"
        Write-ProfileKeyValue -Key "URL" -Value $DashUrl -Style "Link"
    } else {
        Write-ProfileNeutral -Message "Dashboard Server" -Detail "STOPPED (Port $Port is free)"
    }
    Write-Host ""

    # 2. Live Trading Cockpit Status
    Write-ProfileSection -Title "Live Trading Cockpit Engine"
    if ($isListening) {
        try {
            $state = Invoke-RestMethod -Uri "$DashUrl/api/live/state" -UseBasicParsing -TimeoutSec 3
            if ($state.active) {
                Write-ProfileSuccess -Message "Trading Engine" -Detail "ACTIVE (Mode: $($state.mode))"
            } else {
                Write-ProfileInfo -Message "Trading Engine" -Detail "STANDBY (Mode: $($state.mode))"
            }
            if ($null -ne $state.starting_capital) {
                Write-ProfileKeyValue -Key "Starting Capital" -Value ("${0:N2}" -f $state.starting_capital) -Style "Value"
            }
            if ($null -ne $state.portfolio_value) {
                Write-ProfileKeyValue -Key "Portfolio Value" -Value ("${0:N2}" -f $state.portfolio_value) -Style "Value"
            }
            $openOrders = if ($state.orders) { $state.orders.Count } else { 0 }
            Write-ProfileKeyValue -Key "Open Orders" -Value "$openOrders" -Style "Info"
        } catch {
            Write-ProfileWarning -Message "Trading Engine" -Detail "Could not query /api/live/state ($_)"
        }
    } else {
        Write-ProfileNeutral -Message "Trading Engine" -Detail "OFFLINE (Start dashboard to inspect)"
    }
    Write-Host ""

    # 3. Background Collector Status
    Write-ProfileSection -Title "Tick Collector Engine"
    if ($isListening) {
        try {
            $coll = Invoke-RestMethod -Uri "$DashUrl/api/collector/status" -UseBasicParsing -TimeoutSec 3
            if ($coll.running) {
                Write-ProfileSuccess -Message "Collector" -Detail "RUNNING (PID $($coll.pid), Today Ticks: $($coll.total_ticks_collected))"
            } else {
                Write-ProfileInfo -Message "Collector" -Detail "IDLE (Today Ticks: $($coll.total_ticks_collected))"
            }
            if ($null -ne $coll.tape_empty_rate) {
                $emptyPct = [math]::Round($coll.tape_empty_rate * 100, 2)
                Write-ProfileKeyValue -Key "Tape Empty Rate" -Value "$emptyPct%" -Style "Info"
            }
        } catch {
            Write-ProfileWarning -Message "Collector" -Detail "Could not query /api/collector/status"
        }
    } else {
        Write-ProfileNeutral -Message "Collector" -Detail "OFFLINE (Start dashboard to inspect)"
    }
    Write-Host ""

    # 4. Tick Store & Manifest Summary
    Write-ProfileSection -Title "Tick Data Store (run/ticks)"
    $manifestPath = Join-Path $TicksDir "manifest.json"
    $tickFiles = Get-ChildItem $TicksDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in ".jsonl", ".gz" -or $_.Name -like "*.jsonl.gz" }
    
    Write-ProfileKeyValue -Key "Tick Directory" -Value $TicksDir -Style "Path"
    Write-ProfileKeyValue -Key "Total Files" -Value "$($tickFiles.Count)" -Style "Info"
    
    if (Test-Path $manifestPath) {
        try {
            $mf = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-ProfileKeyValue -Key "Manifest Date" -Value "$($mf.day)" -Style "Neutral"
            Write-ProfileKeyValue -Key "Series Tracked" -Value "$($mf.series_seen.Count)" -Style "Info"
            Write-ProfileKeyValue -Key "Sample Ticks" -Value "$($mf.lines)" -Style "Success"
            Write-ProfileKeyValue -Key "Tape Entries" -Value "$($mf.tape_entries_total)" -Style "Success"
        } catch {
            Write-ProfileWarning -Message "Tick Store" -Detail "Unreadable manifest.json"
        }
    }
    Write-Host ""
}

# ── Placeholders for open / stop / compare ──
function Host-Dashboard {
    Write-ProfileInfo -Message "Host-Dashboard" -Detail "Will be fully implemented in Task 2"
}

function Stop-DashboardProcess {
    Write-ProfileInfo -Message "Stop-DashboardProcess" -Detail "Will be fully implemented in Task 3"
}

function Start-PriceMonitor {
    Write-ProfileInfo -Message "Start-PriceMonitor" -Detail "Will be fully implemented in Task 4"
}

# ── Menu Dispatcher ──
switch -Exact ($Action.ToLower()) {
    "status"  { Show-SystemStatus; exit 0 }
    "open"    { Host-Dashboard; exit 0 }
    "stop"    { Stop-DashboardProcess; exit 0 }
    "compare" { Start-PriceMonitor; exit 0 }
    ""        {
        # Interactive loop
        while ($true) {
            Write-Host ""
            Write-ProfileBanner -Title "CRYPTO SPREAD — CONTROL CENTER" -Subtitle "5m/15m SPREAD-2 Capture Operations"
            Write-Host "  [1] Check System Status" -ForegroundColor Cyan
            Write-Host "  [2] Host & Open Dashboard (Background)" -ForegroundColor Green
            Write-Host "  [3] Stop Dashboard & Clean Up Processes" -ForegroundColor Red
            Write-Host "  [4] Live Binance Spot vs. CLOB Book Monitor" -ForegroundColor Yellow
            Write-Host "  [q] Exit" -ForegroundColor DarkGray
            Write-Host ""
            $choice = Read-Host "Select option [1-4, q]"
            switch ($choice.Trim().ToLower()) {
                "1" { Show-SystemStatus }
                "2" { Host-Dashboard }
                "3" { Stop-DashboardProcess }
                "4" { Start-PriceMonitor }
                "q" { Write-ProfileInfo -Message "Exiting Control Center."; exit 0 }
                default { Write-ProfileWarning -Message "Invalid choice: '$choice'" }
            }
        }
    }
    default {
        Write-ProfileError -Message "Unknown action '$Action'." -Detail "Valid actions: status, open, stop, compare"
        exit 1
    }
}
