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
    [switch]$Yes,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$Remaining
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

# Ensure PYTHONPATH includes project root for python -m invocations
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$ProjectPath;$env:PYTHONPATH" } else { $ProjectPath }

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
    function Write-ProfileRuleWithText { param([string]$Text, [string]$Style = "Neutral") Write-Host ("--- $Text " + ("-" * [Math]::Max(5, (75 - $Text.Length)))) -ForegroundColor (Get-ProfileColor -Name Border) }
}

# ── Console helpers (spread-hunter-menu style thin wrappers) ──
function Csm-Banner {
    param([string]$Title, [string]$Subtitle)
    try { Clear-Host } catch {}
    $cBorder = Get-ProfileColor -Name Border
    $cTitle  = [ConsoleColor]::Yellow
    $cSub    = Get-ProfileColor -Name Neutral
    $w = 78
    Write-Host ("╔" + ("═" * $w) + "╗") -ForegroundColor $cBorder
    Write-Host "║ " -ForegroundColor $cBorder -NoNewline
    Write-Host $Title.PadRight($w - 2) -ForegroundColor $cTitle -NoNewline
    Write-Host " ║" -ForegroundColor $cBorder
    if ($Subtitle) {
        Write-Host "║ " -ForegroundColor $cBorder -NoNewline
        Write-Host $Subtitle.PadRight($w - 2) -ForegroundColor $cSub -NoNewline
        Write-Host " ║" -ForegroundColor $cBorder
    }
    Write-Host ("╚" + ("═" * $w) + "╝") -ForegroundColor $cBorder
    Write-Host ""
}
function Csm-Step   { param([string]$Msg) Write-ProfileInfo -Message $Msg }
function Csm-Ok     { param([string]$Msg) Write-ProfileSuccess -Message $Msg }
function Csm-Warn   { param([string]$Msg) Write-ProfileWarning -Message $Msg }
function Csm-Fail   { param([string]$Msg) Write-ProfileError -Message $Msg }
function Csm-Phase  { param([string]$Text) Write-Host ""; Write-ProfileRuleWithText -Text $Text -Style "Info"; Write-Host "" }

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

function Adopt-DashboardInstance {
    param([int]$ExpectedPid)
    <# Record running dashboard process on port 8802 as owned by this menu.
       $ExpectedPid is the port owner observed BEFORE the HTTP probe; adoption
       is refused if the port changed hands since (TOCTOU guard), so a foreign
       process can never be recorded (and later force-killed) as ours. #>
    $portPid = Get-PortPid
    if (-not $portPid) { return $false }
    if ($ExpectedPid -gt 0 -and $portPid -ne $ExpectedPid) { return $false }
    try {
        $proc = Get-Process -Id $portPid -ErrorAction Stop
        Save-DashInstance -DashProcess $proc
    } catch {
        return $false
    }
    return ($null -ne (Get-DashInstance))
}

# ── Status Action ──
function Show-SystemStatus {
    Csm-Banner -Title "CRYPTO SPREAD — TELEMETRY & SYSTEM STATUS" -Subtitle "5m/15m BTC/ETH/BNB/SOL/XRP Spread Capture Lab"
    
    # 1. Dashboard Process & Port Status
    Write-ProfileSection -Title "Dashboard Server (:8802)"
    $isListening = Test-Port
    $inst = Get-DashInstance
    $portPid = Get-PortPid

    if ($inst) {
        Csm-Ok "Dashboard Server RUNNING (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))"
        Write-ProfileKeyValue -Key "URL" -Value $DashUrl -Style "Link" -KeyWidth 22
        Write-ProfileKeyValue -Key "PID Registry" -Value $DashPidFile -Style "Path" -KeyWidth 22
    } elseif ($isListening) {
        Csm-Warn "Dashboard Server LISTENING (PID $portPid, unowned by menu registry)"
        Write-ProfileKeyValue -Key "URL" -Value $DashUrl -Style "Link" -KeyWidth 22
    } else {
        Write-ProfileNeutral -Message "Dashboard Server" -Detail "STOPPED (Port $Port is free)"
    }
    Write-Host ""

    # 2. Live Trading Cockpit Status
    Write-ProfileSection -Title "Live Trading Cockpit Engine"
    if ($isListening) {
        try {
            $state = Invoke-RestMethod -Uri "$DashUrl/api/live/state" -UseBasicParsing -TimeoutSec 3
            if ($state.is_running -or $state.active) {
                Csm-Ok "Trading Engine ACTIVE (Mode: $($state.mode))"
            } else {
                Write-ProfileInfo -Message "Trading Engine" -Detail "STANDBY (Mode: $($state.mode))"
            }
            if ($null -ne $state.starting_capital) {
                Write-ProfileKeyValue -Key "Starting Capital" -Value ("${0:N2}" -f $state.starting_capital) -Style "Value" -KeyWidth 22
            }
            if ($null -ne $state.portfolio_value) {
                Write-ProfileKeyValue -Key "Portfolio Value" -Value ("${0:N2}" -f $state.portfolio_value) -Style "Value" -KeyWidth 22
            }
            $openOrders = if ($state.orders) { $state.orders.Count } else { 0 }
            Write-ProfileKeyValue -Key "Open Orders" -Value "$openOrders" -Style "Info" -KeyWidth 22
        } catch {
            Csm-Warn "Trading Engine: Could not query /api/live/state ($_)"
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
                Csm-Ok "Collector RUNNING (PID $($coll.pid), Today Ticks: $($coll.total_ticks_collected))"
            } else {
                Write-ProfileInfo -Message "Collector" -Detail "IDLE (Today Ticks: $($coll.total_ticks_collected))"
            }
            if ($null -ne $coll.tape_empty_rate) {
                $emptyPct = [math]::Round($coll.tape_empty_rate * 100, 2)
                Write-ProfileKeyValue -Key "Tape Empty Rate" -Value "$emptyPct%" -Style "Info" -KeyWidth 22
            }
        } catch {
            Csm-Warn "Collector: Could not query /api/collector/status"
        }
    } else {
        Write-ProfileNeutral -Message "Collector" -Detail "OFFLINE (Start dashboard to inspect)"
    }
    Write-Host ""

    # 4. Tick Store & Manifest Summary
    Write-ProfileSection -Title "Tick Data Store (run/ticks)"
    $manifestPath = Join-Path $TicksDir "manifest.json"
    $tickFiles = Get-ChildItem $TicksDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in ".jsonl", ".gz" -or $_.Name -like "*.jsonl.gz" }
    
    Write-ProfileKeyValue -Key "Tick Directory" -Value $TicksDir -Style "Path" -KeyWidth 22
    Write-ProfileKeyValue -Key "Total Files" -Value "$($tickFiles.Count)" -Style "Info" -KeyWidth 22
    
    if (Test-Path $manifestPath) {
        try {
            $mf = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-ProfileKeyValue -Key "Manifest Date" -Value "$($mf.day)" -Style "Neutral" -KeyWidth 22
            Write-ProfileKeyValue -Key "Series Tracked" -Value "$($mf.series_seen.Count)" -Style "Info" -KeyWidth 22
            Write-ProfileKeyValue -Key "Sample Ticks" -Value "$($mf.lines)" -Style "Success" -KeyWidth 22
            Write-ProfileKeyValue -Key "Tape Entries" -Value "$($mf.tape_entries_total)" -Style "Success" -KeyWidth 22
        } catch {
            Csm-Warn "Tick Store: Unreadable manifest.json"
        }
    }
    Write-Host ""
}

# ── Host Dashboard Action ──
function Host-Dashboard {
    $inst = Get-DashInstance
    if ($null -ne $inst) {
        Csm-Ok "Dashboard already running (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
        return $true
    }
    if (Test-Port) {
        $portPid = Get-PortPid
        if (Test-DashboardServer) {
            # Any terminal from anywhere can adopt a verified dashboard.
            # Auto-adopt (same as crypto-spread-isolated.ps1) so a stale/missing
            # run/dash.pids.json never orphans a live dashboard.
            if (Adopt-DashboardInstance -ExpectedPid $portPid) {
                $inst = Get-DashInstance
                Csm-Ok "Adopted dashboard (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
                return $true
            }
            Csm-Ok "Dashboard is serving on ${DashUrl} (PID $portPid, adoption record write failed but port is verified)."
            return $true
        }
        Csm-Fail "Port $Port occupied: PID $portPid does NOT answer as a crypto-spread dashboard. Free the port manually first."
        return $false
    }
    
    Csm-Step "Launching dashboard (python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port $Port)..."
    $dash = Start-Process -FilePath "python" `
        -ArgumentList "-m", "uvicorn", "server.osc_dash:app", "--host", "127.0.0.1", "--port", "$Port" `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError  $ErrLog
    Save-DashInstance -DashProcess $dash

    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-Port) { break }
        $dash.Refresh()
        if ($dash.HasExited) { break }
    }
    if (-not (Test-Port)) {
        Csm-Fail "Dashboard failed to bind port $Port. See $ErrLog"
        Remove-Item $DashPidFile -ErrorAction SilentlyContinue
        return $false
    }
    Csm-Ok "Dashboard serving on $DashUrl (PID $($dash.Id))."
    try { Start-Process $DashUrl } catch {}
    return $true
}

function Wait-ProcessGone {
    param([int]$ProcessId, [int]$TimeoutSec = 10)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return $true
        }
        Start-Sleep -Milliseconds 300
    }
    return (-not [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue))
}

function Stop-DashboardProcess {
    # Any terminal from anywhere can stop the dashboard: prefer the registry
    # PID, but fall back to the port owner when the registry is stale/missing
    # (orphaned dashboard, cleaned run/, different shell). Only kill the port
    # owner after it verifies as our dashboard to avoid killing foreign services.
    $inst = Get-DashInstance
    $portPid = Get-PortPid
    $targetPid = if ($inst) { $inst.pid } else { $portPid }

    if ($targetPid) {
        if ($null -eq $inst) {
            if (Test-DashboardServer) {
                Adopt-DashboardInstance -ExpectedPid $portPid | Out-Null
                $inst = Get-DashInstance
                $targetPid = if ($inst) { $inst.pid } else { $portPid }
                Csm-Step "Adopted orphaned dashboard PID $targetPid (no registry record)..."
            } else {
                Csm-Fail "Port $Port occupied by PID $portPid which does NOT answer as a crypto-spread dashboard. Refusing to kill a foreign process."
                return $false
            }
        }
        # TOCTOU guard: before a force-kill of the port owner, the port must
        # still belong to the PID we validated (registry path is already
        # start-ticks verified; this protects the port-owner fallback).
        if ($null -eq $inst -or $targetPid -eq $portPid) {
            $currentPortPid = Get-PortPid
            if ($currentPortPid -and $currentPortPid -ne $targetPid) {
                Csm-Fail "Port $Port changed hands (PID $targetPid -> $currentPortPid) since validation. Refusing to kill a different process."
                return $false
            }
        }
        Csm-Step "Stopping dashboard PID $targetPid..."
        taskkill /F /T /PID $targetPid 2>$null | Out-Null
        if (Wait-ProcessGone -ProcessId $targetPid) {
            Csm-Ok "Dashboard process tree stopped."
            Remove-Item $DashPidFile -ErrorAction SilentlyContinue
        } else {
            Csm-Warn "Dashboard PID $targetPid did not exit cleanly; PID record kept."
        }
    } else {
        Remove-Item $DashPidFile -ErrorAction SilentlyContinue
    }
    if (Test-Port) {
        $stillPid = Get-PortPid
        if (Test-DashboardServer) {
            Csm-Warn "Port $Port still LISTENING (PID $stillPid) — stop retried but process survived."
        } else {
            Csm-Warn "Port $Port still LISTENING (PID $stillPid) — now owned by a foreign process, left running."
        }
        return $false
    }
    Csm-Ok "Port $Port free."
    return $true
}

function Start-PriceMonitor {
    param([string[]]$MonitorArgs)
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$ProjectPath;$env:PYTHONPATH" } else { $ProjectPath }
    Csm-Banner -Title "CRYPTO SPREAD — LIVE BINANCE SPOT vs CLOB BOOK MONITOR" -Subtitle "Side-by-Side Real-Time Tick Stream & Latency Audit"
    Csm-Step "Starting live stream monitor (python -X utf8 -m scripts.monitor_stream_latency $($MonitorArgs -join ' '))..."
    Write-Host ""
    Push-Location $ProjectPath
    try {
        & python -X utf8 -m scripts.monitor_stream_latency @MonitorArgs
    } finally {
        Pop-Location
    }
}

# ── Menu Grid Renderer (spread-hunter-menu style) ──
function Show-MenuGrid {
    $cInfo    = Get-ProfileColor -Name Info
    $cStrong  = Get-ProfileColor -Name Strong
    $cNeutral = Get-ProfileColor -Name Neutral

    Write-Host "  CRYPTO SPREAD — CONTROL CENTER" -ForegroundColor $cInfo
    Write-Host ("  " + ("─" * 32)) -ForegroundColor (Get-ProfileColor -Name Border)
    Write-Host ""

    $groups = @(
        @{ Header = "🟢 DASHBOARD & TELEMETRY"; Items = @(
            @{ K = "1"; Icon = "≡"; IconColor = "Info";      V = "Check System Status";        D = "System telemetry, collector status & tick store metrics" }
            @{ K = "2"; Icon = "▶"; IconColor = "Success";   V = "Host & Open Dashboard";     D = "Hosts background dashboard on :8802 & opens browser" }
            @{ K = "3"; Icon = "■"; IconColor = "Error";     V = "Stop Dashboard Process";     D = "Stops dashboard process tree & frees port 8802" }
        ) }
        @{ Header = "⚡ REAL-TIME MONITORING"; Items = @(
            @{ K = "4"; Icon = "◈"; IconColor = "Highlight"; V = "Spot vs CLOB Monitor";      D = "Side-by-side Binance spot vs Polymarket CLOB book ticks stream" }
        ) }
    )

    foreach ($g in $groups) {
        Write-Host ("  " + $g.Header) -ForegroundColor $cInfo
        foreach ($it in $g.Items) {
            Write-Host "   " -NoNewline
            Write-Host (" {0} " -f $it.K) -BackgroundColor (Get-ProfileColor -Name Border) -ForegroundColor Black -NoNewline
            Write-Host ("  {0} " -f $it.Icon) -ForegroundColor (Get-ProfileColor -Name $it.IconColor) -NoNewline
            Write-Host ("{0,-28}" -f $it.V) -ForegroundColor $cStrong -NoNewline
            Write-Host $it.D -ForegroundColor $cNeutral
        }
        Write-Host ""
    }
    Write-Host "  q  × Exit · Return to PowerShell" -ForegroundColor $cNeutral
}

function Invoke-MenuAction {
    param([string]$Key)
    switch ($Key) {
        "1" { Show-SystemStatus }
        "2" { Host-Dashboard }
        "3" { Stop-DashboardProcess }
        "4" { Start-PriceMonitor }
        "q" { Csm-Step "Exiting Control Center."; exit 0 }
        default {
            Csm-Warn "Invalid selection: '$Key' (choose 1-4, or q)."
            Start-Sleep -Seconds 1
        }
    }
}

# ── Menu Dispatcher ──
if ($Action -ne "") {
    $actionMap = @{
        "1"            = "1"
        "status"       = "1"
        "get"          = "1"
        "2"            = "2"
        "open"         = "2"
        "host"         = "2"
        "dash"         = "2"
        "dashboard"    = "2"
        "3"            = "3"
        "stop"         = "3"
        "clean"        = "3"
        "kill"         = "3"
        "4"            = "4"
        "compare"      = "4"
        "monitor"      = "4"
        "stream"       = "4"
    }
    $key = $Action.Trim().ToLower()
    if ($actionMap.ContainsKey($key)) { $key = $actionMap[$key] }

    switch ($key) {
        "1" { Show-SystemStatus; exit 0 }
        "2" { if (Host-Dashboard) { exit 0 } else { exit 1 } }
        "3" { if (Stop-DashboardProcess) { exit 0 } else { exit 1 } }
        "4" { Start-PriceMonitor -MonitorArgs $Remaining; exit 0 }
        default {
            Csm-Fail "Unknown action '$Action'. Valid actions: status, open, stop, compare"
            exit 1
        }
    }
}

# Interactive loop
while ($true) {
    Csm-Banner -Title "CRYPTO SPREAD — CONTROL CENTER" -Subtitle "5m/15m SPREAD-2 Capture Operations"
    Show-MenuGrid
    Write-Host "  Select " -ForegroundColor (Get-ProfileColor -Name Text) -NoNewline
    Write-Host "[1-4, q]" -ForegroundColor (Get-ProfileColor -Name Command) -NoNewline
    Write-Host " › " -ForegroundColor (Get-ProfileColor -Name Highlight) -NoNewline
    $choice = Read-Host
    if ($null -eq $choice) { exit 0 }
    $choice = $choice.Trim().ToLower()
    if ($choice -eq "") { exit 0 }
    Invoke-MenuAction $choice

    Write-Host ""
    Write-Host "  Press Enter to return to main menu..." -ForegroundColor (Get-ProfileColor -Name Neutral)
    $null = Read-Host
}

