# novel-mind local keep-alive: ensure backend :8010 + frontend :3005 stay up.
# Launch via start-keep-alive.bat (or WMI) so this process is outside agent Job Object.
# Backend uses 8010 to avoid conflict with other tools (e.g. rag-api) on :8000.

$ErrorActionPreference = "Continue"
# 只从脚本所在位置推导仓库根目录（已移除硬编码本机路径，仓库公开可读）
$Root = Split-Path $PSScriptRoot -Parent
$CandidateRoots = @($Root) | Select-Object -Unique

function Resolve-BackendPython([string]$RepoRoot) {
    foreach ($rel in @("backend\.venv\Scripts\python.exe", "backend\venv\Scripts\python.exe")) {
        $p = Join-Path $RepoRoot $rel
        if (Test-Path $p) { return $p }
    }
    return $null
}

$Py = $null
foreach ($r in $CandidateRoots) {
    $found = Resolve-BackendPython $r
    if ($found -and (Test-Path (Join-Path $r "frontend\package.json"))) {
        $Root = $r
        $Py = $found
        break
    }
}
if (-not $Py) {
    $Root = $CandidateRoots[0]
    $Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
}

$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$AgentDir = Join-Path $Root "agent-service"
$LogFile = Join-Path $PSScriptRoot "keep-alive.log"
$PidFile = Join-Path $PSScriptRoot "keep-alive.pid"
$IntervalSec = 8
$BePort = 8010
$FePort = 3005
$AgPort = 3100
$BeHealth = "http://127.0.0.1:$BePort/api/health"
$FeUrl = "http://127.0.0.1:$FePort/"
$AgHealth = "http://127.0.0.1:$AgPort/healthz"

$env:NO_PROXY = "127.0.0.1,localhost"
$env:no_proxy = "127.0.0.1,localhost"

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Test-HttpOk([string]$Url, [int]$TimeoutMs = 4000) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Method = "GET"
        $req.Timeout = $TimeoutMs
        $req.Proxy = $null
        $req.KeepAlive = $false
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return ($code -ge 200 -and $code -lt 400)
    } catch {
        return $false
    }
}

function Get-Listener([int]$Port) {
    return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-ProcessCommand([int]$ProcessId) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($p) { return [string]$p.CommandLine }
    return ""
}

function Test-IsNovelMindBackend([string]$Cmd) {
    if (-not $Cmd) { return $false }
    return (
        $Cmd -match 'novel-mind[\\/]+backend[\\/]+(\.venv|venv)' -and
        $Cmd -match 'uvicorn'
    ) -or (
        $Cmd -match 'uvicorn app\.main:app' -and
        $Cmd -match ":$BePort|port $BePort|--port $BePort"
    )
}

function Test-IsNovelMindFrontend([string]$Cmd) {
    if (-not $Cmd) { return $false }
    return ($Cmd -match 'novel-mind[\\/]+frontend' -and ($Cmd -match 'next' -or $Cmd -match 'start-server'))
}

function Test-IsNovelMindAgent([string]$Cmd) {
    if (-not $Cmd) { return $false }
    return ($Cmd -match 'agent-service' -and ($Cmd -match 'node' -or $Cmd -match 'start.mjs'))
}

function Stop-ProcessTree([int]$ProcessId) {
    if ($ProcessId -le 4) { return }
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-ProcessTree -ProcessId $_.ProcessId }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Clear-ForeignPort([int]$Port, [scriptblock]$IsOurs) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($l in $listeners) {
        $cmd = Get-ProcessCommand -ProcessId $l.OwningProcess
        if (& $IsOurs $cmd) { continue }
        Write-Log "Reclaim :$Port from PID $($l.OwningProcess): $cmd"
        Stop-ProcessTree -ProcessId $l.OwningProcess
    }
    Start-Sleep -Milliseconds 500
}

function Start-DetachedCmd([string]$Title, [string]$BatPath, [string]$Body) {
    Set-Content -Path $BatPath -Value $Body -Encoding ASCII
    # WMI + start = new console, outside agent job
    $create = ([wmiclass]"Win32_Process").Create("cmd.exe /c start `"$Title`" /MIN `"$BatPath`"")
    Write-Log "Detached start $Title Return=$($create.ReturnValue) PID=$($create.ProcessId)"
}

function Start-Backend {
    if (-not (Test-Path $Py)) {
        Write-Log "ERROR missing python: $Py"
        return
    }
    Clear-ForeignPort -Port $BePort -IsOurs { param($c) Test-IsNovelMindBackend $c }

    $bat = Join-Path $BackendDir "_keep_be.bat"
    $outLog = Join-Path $BackendDir "uvicorn.out.log"
    $body = @"
@echo off
cd /d "$BackendDir"
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
set HTTPS_PROXY=http://127.0.0.1:7897
set HTTP_PROXY=http://127.0.0.1:7897
"$Py" -m uvicorn app.main:app --host 127.0.0.1 --port $BePort >> "$outLog" 2>&1
"@
    Start-DetachedCmd -Title "novelmind-be" -BatPath $bat -Body $body
}

function Start-Frontend {
    Clear-ForeignPort -Port $FePort -IsOurs { param($c) Test-IsNovelMindFrontend $c }

    $bat = Join-Path $FrontendDir "_keep_fe.bat"
    $outLog = Join-Path $FrontendDir "runtime-frontend.log"
    $body = @"
@echo off
cd /d "$FrontendDir"
set BACKEND_URL=http://127.0.0.1:$BePort
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
rem next/font downloader fails through the local proxy; frontend must connect directly
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
rem Production mode: requires `npm run build` output in .next (rebuild after frontend changes)
call npm run start -- --port $FePort --hostname 127.0.0.1 >> "$outLog" 2>&1
"@
    Start-DetachedCmd -Title "novelmind-fe" -BatPath $bat -Body $body
}

function Start-AgentService {
    Clear-ForeignPort -Port $AgPort -IsOurs { param($c) Test-IsNovelMindAgent $c }
    $bat = Join-Path $AgentDir "_keep_agent.bat"
    $outLog = Join-Path $AgentDir "runtime-agent.log"
    $body = @"
@echo off
cd /d "$AgentDir"
set NOVELMIND_GATEWAY_TOKEN=dev-agent-gateway-token-local
set FASTAPI_BASE_URL=http://127.0.0.1:$BePort
set PORT=$AgPort
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
node start.mjs >> "$outLog" 2>&1
"@
    Start-DetachedCmd -Title "novelmind-agent" -BatPath $bat -Body $body
}

function Ensure-AgentService {
    $listener = Get-Listener -Port $AgPort
    if ($listener -and (Test-HttpOk $AgHealth)) { return }
    if ($listener) {
        $cmd = Get-ProcessCommand -ProcessId $listener.OwningProcess
        Write-Log "Agent unhealthy; restart PID $($listener.OwningProcess) $cmd"
        Stop-ProcessTree -ProcessId $listener.OwningProcess
        Start-Sleep -Milliseconds 600
    } else {
        Write-Log "Agent down"
    }
    Start-AgentService
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 2
        if (Test-HttpOk $AgHealth) {
            Write-Log "Agent healthy"
            return
        }
    }
    Write-Log "Agent still unhealthy"
}

function Ensure-Backend {
    $listener = Get-Listener -Port $BePort
    $cmd = if ($listener) { Get-ProcessCommand -ProcessId $listener.OwningProcess } else { "" }
    $ours = Test-IsNovelMindBackend $cmd

    if ($ours -and (Test-HttpOk $BeHealth)) { return }

    if ($listener -and -not $ours) {
        Write-Log "Backend port stolen: $cmd"
        Clear-ForeignPort -Port $BePort -IsOurs { param($c) Test-IsNovelMindBackend $c }
    } elseif ($listener -and $ours) {
        Write-Log "Backend health failed; restart PID $($listener.OwningProcess)"
        Stop-ProcessTree -ProcessId $listener.OwningProcess
        Start-Sleep -Milliseconds 600
    } else {
        Write-Log "Backend down"
    }

    Start-Backend
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 2
        if (Test-HttpOk $BeHealth) {
            Write-Log "Backend healthy"
            return
        }
        $l2 = Get-Listener -Port $BePort
        if ($l2) {
            $c2 = Get-ProcessCommand -ProcessId $l2.OwningProcess
            if (-not (Test-IsNovelMindBackend $c2)) {
                Write-Log "Mid-start reclaim :$BePort from: $c2"
                Clear-ForeignPort -Port $BePort -IsOurs { param($c) Test-IsNovelMindBackend $c }
                Start-Backend
            }
        }
    }
    Write-Log "Backend still unhealthy"
}

function Ensure-Frontend {
    $listener = Get-Listener -Port $FePort
    if ($listener -and (Test-HttpOk $FeUrl 8000)) { return }

    if ($listener) {
        $cmd = Get-ProcessCommand -ProcessId $listener.OwningProcess
        Write-Log "Frontend unhealthy; restart PID $($listener.OwningProcess) $cmd"
        Stop-ProcessTree -ProcessId $listener.OwningProcess
        Start-Sleep -Milliseconds 600
    } else {
        Write-Log "Frontend down"
    }

    Start-Frontend
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 2
        if (Test-HttpOk $FeUrl 8000) {
            Write-Log "Frontend healthy"
            return
        }
    }
    Write-Log "Frontend still unhealthy"
}

# --- single instance ---
if (Test-Path $PidFile) {
    $old = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1) -as [int]
    if ($old -and $old -ne $PID -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Log "Another keep-alive running PID=$old; exit"
        exit 0
    }
}
Set-Content -Path $PidFile -Value $PID -Encoding ASCII
Write-Log "keep-alive start PID=$PID root=$Root every ${IntervalSec}s"

try {
    while ($true) {
        try {
            Ensure-Backend
            Ensure-Frontend
            Ensure-AgentService
        } catch {
            Write-Log "Loop error: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds $IntervalSec
    }
} finally {
    if ((Test-Path $PidFile) -and ((Get-Content $PidFile -Raw).Trim() -eq "$PID")) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Log "keep-alive stop PID=$PID"
}
