# CRYPTO SPREAD - ISOLATED DASHBOARD CONTROL
# Dedicated controller for the isolated paper runner & dashboard on port 8888
# (Running out of crypto-spread-paper worktree, completely decoupled from dev repo).
#
# Usage:
#   .\scripts\crypto-spread-isolated.ps1 status   # [1] check isolated system status & telemetry
#   .\scripts\crypto-spread-isolated.ps1 open     # [2] host & open isolated dashboard (:8888)
#   .\scripts\crypto-spread-isolated.ps1 stop     # [3] stop isolated dashboard process tree
#   .\scripts\crypto-spread-isolated.ps1 sync     # [4] sync latest master commits into isolated worktree
#




[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = "",
    [switch]$Yes,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$Remaining
)

$ErrorActionPreference = "Stop"
$CurrentDir   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$IsolatedPath = if (Test-Path (Join-Path $CurrentDir "..\crypto-spread-paper")) {
    (Resolve-Path (Join-Path $CurrentDir "..\crypto-spread-paper")).Path
} elseif ((Split-Path -Leaf $CurrentDir) -eq "crypto-spread-paper") {
    $CurrentDir
} else {
    $CurrentDir
}

$Port        = 8888
$DashUrl     = "http://127.0.0.1:$Port"
$RunDir      = Join-Path $IsolatedPath "run"
$TicksDir    = Join-Path $RunDir "ticks"
$LogDir      = Join-Path $IsolatedPath "logs"
$DashPidFile = Join-Path $RunDir "dash.pids.json"
$OutLog      = Join-Path $LogDir "dash.out.log"
$ErrLog      = Join-Path $LogDir "dash.err.log"

# Ensure runtime directory exists
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
New-Item -ItemType Directory -Force -Path $TicksDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

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
        param([string]$Key, [string]$Value, [string]$Style = "Info", [int]$KeyWidth = 22)
        $k = ("{0,-$KeyWidth}" -f $Key)
        Write-Host "  $k" -ForegroundColor (Get-ProfileColor -Name Neutral) -NoNewline
        Write-Host " : $Value" -ForegroundColor (Get-ProfileColor -Name $Style)
    }
    function Write-ProfileRuleWithText { param([string]$Text, [string]$Style = "Neutral") Write-Host ("--- $Text " + ("-" * [Math]::Max(5, (75 - $Text.Length)))) -ForegroundColor (Get-ProfileColor -Name Border) }
}

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

# ── Process & Network Primitives ──
function Test-Port {
    return [bool](netstat -ano | Select-String ":$Port\s+.*LISTENING")
}

function Get-PortPid {
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
    if (-not (Test-Path $DashPidFile)) { return $null }
    try { $data = Get-Content $DashPidFile -Raw | ConvertFrom-Json } catch { return $null }
    $d = $data.dash
    if (-not $d -or -not $d.pid) { return $null }
    try { $p = Get-Process -Id $d.pid -ErrorAction Stop } catch { return $null }
    if ($null -ne $d.started_ticks) {
        $pStart = $null
        try { $pStart = $p.StartTime } catch {}
        if ($null -eq $pStart -or $pStart.ToUniversalTime().Ticks -ne [int64]$d.started_ticks) {
            return $null
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
        strategy = "crypto-spread-isolated"
        workspace = $IsolatedPath
        saved    = (Get-Date).ToString("o")
        dash     = $record
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $DashPidFile -Encoding UTF8
}

function Test-DashboardServer {
    try {
        $r = Invoke-RestMethod -Uri "$DashUrl/api/oscillation" -UseBasicParsing -TimeoutSec 3
        return ($null -ne $r -and $null -ne $r.summary)
    } catch {
        return $false
    }
}

function Adopt-DashboardInstance {
    $portPid = Get-PortPid
    if (-not $portPid) { return $false }
    try {
        $proc = Get-Process -Id $portPid -ErrorAction Stop
        Save-DashInstance -DashProcess $proc
    } catch {
        return $false
    }
    return ($null -ne (Get-DashInstance))
}

function Show-SystemStatus {
    Csm-Banner -Title "CRYPTO SPREAD — ISOLATED DASHBOARD STATUS" -Subtitle "Independent Paper Runner & Cockpit on :$Port"

    Write-ProfileSection -Title "Isolated Dashboard Server (:$Port)"
    $isListening = Test-Port
    $inst = Get-DashInstance
    $portPid = Get-PortPid

    if ($inst) {
        Csm-Ok "Isolated Server RUNNING (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))"
        Write-ProfileKeyValue -Key "URL" -Value $DashUrl -Style "Link"
        Write-ProfileKeyValue -Key "Workspace" -Value $IsolatedPath -Style "Path"
        Write-ProfileKeyValue -Key "PID Registry" -Value $DashPidFile -Style "Path"
    } elseif ($isListening) {
        Csm-Warn "Isolated Server LISTENING (PID $portPid, unowned by registry)"
        Write-ProfileKeyValue -Key "URL" -Value $DashUrl -Style "Link"
        Write-ProfileKeyValue -Key "Workspace" -Value $IsolatedPath -Style "Path"
    } else {
        Write-ProfileNeutral -Message "Isolated Server" -Detail "STOPPED (Port $Port is free)"
        Write-ProfileKeyValue -Key "Workspace" -Value $IsolatedPath -Style "Path"
    }
    Write-Host ""

    Write-ProfileSection -Title "Paper Trading Cockpit Engine (:$Port)"
    if ($isListening) {
        try {
            $state = Invoke-RestMethod -Uri "$DashUrl/api/live/state" -UseBasicParsing -TimeoutSec 3
            if ($state.is_running -or $state.active) {
                Csm-Ok "Trading Engine ACTIVE (Mode: $($state.mode))"
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
            Csm-Warn "Trading Engine: Could not query /api/live/state ($_)"
        }
    } else {
        Write-ProfileNeutral -Message "Trading Engine" -Detail "OFFLINE (Start isolated dashboard to inspect)"
    }
    Write-Host ""

    Write-ProfileSection -Title "Isolated Tick Store & Live Trades"
    $tradesFile = Join-Path $RunDir "live_trades.jsonl"
    if (Test-Path $tradesFile) {
        $tradeCount = (Get-Content $tradesFile | Measure-Object -Line).Lines
        Write-ProfileKeyValue -Key "Recorded Paper Trades" -Value "$tradeCount" -Style "Success"
    } else {
        Write-ProfileKeyValue -Key "Recorded Paper Trades" -Value "0 (No trades yet)" -Style "Neutral"
    }
    $manifestPath = Join-Path $TicksDir "manifest.json"
    if (Test-Path $manifestPath) {
        try {
            $mf = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-ProfileKeyValue -Key "Manifest Date" -Value "$($mf.day)" -Style "Neutral"
            Write-ProfileKeyValue -Key "Sample Ticks" -Value "$($mf.lines)" -Style "Success"
        } catch {}
    }
    Write-Host ""
}

function Host-Dashboard {
    $inst = Get-DashInstance
    if ($null -ne $inst) {
        Csm-Ok "Isolated dashboard already running (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
        try { Start-Process $DashUrl } catch {}
        return $true
    }
    if (Test-Port) {
        $portPid = Get-PortPid
        if (Test-DashboardServer) {
            Adopt-DashboardInstance | Out-Null
            $inst = Get-DashInstance
            Csm-Ok "Adopted running isolated dashboard (PID $portPid)."
            try { Start-Process $DashUrl } catch {}
            return $true
        }
        Csm-Fail "Port $Port occupied by PID $portPid which is not answering as crypto-spread dashboard."
        return $false
    }

    Csm-Step "Launching isolated dashboard on :$Port from $IsolatedPath..."
    $cmd = "cmd.exe /c python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port $Port > `"$OutLog`" 2> `"$ErrLog`""
    $cimRes = $null
    try {
        $cimRes = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine      = $cmd
            CurrentDirectory = $IsolatedPath
        }
    } catch {}

    if ($null -eq $cimRes -or $cimRes.ReturnValue -ne 0) {
        $dash = Start-Process -FilePath "python" `
            -ArgumentList "-m", "uvicorn", "server.osc_dash:app", "--host", "127.0.0.1", "--port", "$Port" `
            -WorkingDirectory $IsolatedPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $OutLog `
            -RedirectStandardError  $ErrLog
        Save-DashInstance -DashProcess $dash
    }

    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-Port) { break }
    }
    if (-not (Test-Port)) {
        Csm-Fail "Isolated dashboard failed to bind port $Port. See $ErrLog"
        Remove-Item $DashPidFile -ErrorAction SilentlyContinue
        return $false
    }
    Adopt-DashboardInstance | Out-Null
    $inst = Get-DashInstance
    $livePid = if ($inst) { $inst.pid } else { Get-PortPid }
    Csm-Ok "Isolated dashboard serving on $DashUrl (PID $livePid)."
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
    $inst = Get-DashInstance
    $portPid = Get-PortPid
    $targetPid = if ($inst) { $inst.pid } else { $portPid }

    if ($targetPid) {
        Csm-Step "Stopping isolated dashboard PID $targetPid..."
        taskkill /F /T /PID $targetPid 2>$null | Out-Null
        if (Wait-ProcessGone -ProcessId $targetPid) {
            Csm-Ok "Isolated dashboard process tree stopped."
            Remove-Item $DashPidFile -ErrorAction SilentlyContinue
        } else {
            Csm-Warn "Process PID $targetPid did not exit cleanly."
        }
    }
    if (Test-Port) {
        Csm-Warn "Port $Port still in use by PID $(Get-PortPid)."
        return $false
    }
    Csm-Ok "Port $Port free."
    return $true
}

function Sync-Worktree {
    Csm-Step "Syncing master branch into isolated worktree ($IsolatedPath)..."
    Push-Location $IsolatedPath
    try {
        & git merge master
        Csm-Ok "Isolated worktree synced to latest master."
    } finally {
        Pop-Location
    }
}

# ── Dispatcher ──
$actionMap = @{
    "1"       = "status"
    "status"  = "status"
    "get"     = "status"
    "2"       = "open"
    "open"    = "open"
    "host"    = "open"
    "dash"    = "open"
    "3"       = "stop"
    "stop"    = "stop"
    "kill"    = "stop"
    "clean"   = "stop"
    "4"       = "sync"
    "sync"    = "sync"
    "update"  = "sync"
}

$key = $Action.Trim().ToLower()
if ($actionMap.ContainsKey($key)) { $key = $actionMap[$key] }

switch ($key) {
    "status" { Show-SystemStatus; exit 0 }
    "open"   { if (Host-Dashboard) { exit 0 } else { exit 1 } }
    "stop"   { if (Stop-DashboardProcess) { exit 0 } else { exit 1 } }
    "sync"   { Sync-Worktree; exit 0 }
    default  {
        Show-SystemStatus
        Write-Host ""
        Write-Host "  Commands available:" -ForegroundColor (Get-ProfileColor -Name Info)
        Write-Host "    .\scripts\crypto-spread-isolated.ps1 open    Host & open isolated dashboard (:8888)" -ForegroundColor (Get-ProfileColor -Name Text)
        Write-Host "    .\scripts\crypto-spread-isolated.ps1 status  Inspect isolated system telemetry" -ForegroundColor (Get-ProfileColor -Name Text)
        Write-Host "    .\scripts\crypto-spread-isolated.ps1 stop    Stop isolated dashboard process" -ForegroundColor (Get-ProfileColor -Name Text)
        Write-Host "    .\scripts\crypto-spread-isolated.ps1 sync    Sync latest master into worktree" -ForegroundColor (Get-ProfileColor -Name Text)
        exit 0
    }
}
