# Restart the PySearch backend cleanly (Windows PowerShell 5.1+).
#
#   1. Shows what is listening on port 8000 and stops it - but ONLY if it is a
#      Python/uvicorn process (any other program on port 8000 is left alone).
#   2. Runs the route check, the Phase 3 API tests and the full test suite.
#   3. Starts:  uvicorn app.main:app --reload
#
# Everything from steps 1-2 is also written to data\backend-check.log.
# Usage: double-click scripts\restart-backend.bat   (or run this .ps1 from PowerShell)

$ErrorActionPreference = 'Continue'
$root    = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$log     = Join-Path $root 'data\backend-check.log'
$py      = Join-Path $backend '.venv\Scripts\python.exe'
$port    = 8000

function Log([string]$msg) {
    Write-Host $msg
    Add-Content -Path $log -Value $msg -Encoding UTF8
}

Set-Content -Path $log -Value "=== PySearch backend restart $(Get-Date -Format s) ===" -Encoding UTF8

if (-not (Test-Path $py)) {
    Log "ERROR: virtual environment not found at $py"
    exit 1
}
Set-Location $backend

# ---------------------------------------------------------------- 1. stop the old server
# PIDs listening on the port, read from netstat. On Windows, uvicorn's
# SO_REUSEADDR lets SEVERAL servers listen on the same port at once (old ones
# keep receiving requests), and Get-NetTCPConnection may report only one of
# them, so netstat is the reliable source.
function Get-Listeners {
    @(netstat -ano | Select-String ":$port\s+\S+\s+LISTENING\s+(\d+)" | ForEach-Object {
        [int]$_.Matches[0].Groups[1].Value
    } | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
}

# WMI lookups can throw *terminating* errors (which silently abort a whole
# loop), so every lookup is wrapped and failures are logged.
function Get-ProcInfo($procId) {
    try { return Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction Stop }
    catch { Log "  (WMI lookup failed for PID ${procId}: $($_.Exception.Message))"; return $null }
}
function Get-ChildProcs($procId) {
    try { return @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction Stop) }
    catch { Log "  (WMI child lookup failed for PID ${procId}: $($_.Exception.Message))"; return @() }
}
function Stop-Tree($procId) {
    Log "  stopping process tree rooted at PID $procId"
    taskkill /PID $procId /T /F 2>&1 | ForEach-Object { Log "  $_" }
}

Log "--- netstat entries for port ${port}:"
netstat -ano | Select-String ":$port\s" | ForEach-Object { Log ("  " + $_.Line.Trim()) }

Log "--- Processes listening on port $port BEFORE restart:"
$listeners = Get-Listeners
if ($listeners.Count -eq 0) { Log "(none)" }

foreach ($id in $listeners) {
    try {
        $p = Get-ProcInfo $id
        $alive = $null -ne (Get-Process -Id $id -ErrorAction SilentlyContinue)
        if (-not $p -and -not $alive) {
            # Windows reports the process that CREATED the socket. If that process
            # (e.g. a uvicorn --reload supervisor) has exited, a child that inherited
            # the socket keeps serving: an orphan. Find and stop those children.
            Log "PID $id owns the port but no longer exists (orphaned socket). Looking for its child processes..."
            $children = Get-ChildProcs $id
            if ($children.Count -eq 0) { Log "  no child processes found" }
            foreach ($c in $children) {
                Log ("  child PID {0}  started {1}  {2}" -f $c.ProcessId, $c.CreationDate, $c.CommandLine)
                if ($c.Name -match '^python') { Stop-Tree $c.ProcessId }
                else { Log "  not a Python process - leaving it alone" }
            }
            continue
        }
        if (-not $p) {
            $gp = Get-Process -Id $id
            Log ("PID {0}  name {1}  started {2}  path {3}" -f $id, $gp.ProcessName, $gp.StartTime, $gp.Path)
            if ($gp.ProcessName -notmatch '^python') {
                Log "ERROR: port $port is used by '$($gp.ProcessName)', not a Python server. Not stopping it."
                exit 1
            }
            Stop-Tree $id
            continue
        }

        Log ("PID {0}  started {1}  {2}" -f $id, $p.CreationDate, $p.CommandLine)
        if ($p.Name -notmatch '^python' -and $p.CommandLine -notmatch 'uvicorn') {
            Log "ERROR: port $port is used by '$($p.Name)', which is not a Python/uvicorn server. Not stopping it."
            exit 1
        }
        # uvicorn --reload runs a supervisor process plus a worker; stop the whole tree
        # starting from the top-most uvicorn ancestor so no orphan keeps the port.
        $top = $p
        $parent = Get-ProcInfo $p.ParentProcessId
        if ($parent -and $parent.CommandLine -match 'uvicorn') {
            Log ("  parent PID {0}: {1}" -f $parent.ProcessId, $parent.CommandLine)
            $top = $parent
        }
        Stop-Tree $top.ProcessId
    }
    catch {
        Log "  ERROR while handling PID ${id}: $($_.Exception.Message)"
    }
}

if ((Get-Listeners).Count -gt 0) {
    Log "--- Python processes currently running (for diagnosis):"
    Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
        Log ("  PID {0}  started {1}  {2}" -f $_.Id, $_.StartTime, $_.Path)
    }
}

# wait (max 10 s) for the port to be released
for ($i = 0; $i -lt 20 -and (Get-Listeners).Count -gt 0; $i++) { Start-Sleep -Milliseconds 500 }
if ((Get-Listeners).Count -gt 0) {
    Log "ERROR: port $port is still in use after stopping. Close other server windows and run this again."
    exit 1
}
Log "--- Port $port is free."

# ---------------------------------------------------------------- 2. checks
Log "--- Python: $py"
# FastAPI >= 0.16x stores include_router() routers as _IncludedRouter objects
# in app.routes (they have no .path), so look inside them.
Log "--- Route list:"
& $py -c "from app.main import app; print([p for r in app.routes for p in ([x.path for x in r.original_router.routes] if hasattr(r, 'original_router') else [r.path])])" 2>&1 | ForEach-Object { Log "$_" }

Log "--- Phase 3 API tests (tests/test_analyze_api.py):"
& $py -m pytest -o addopts= -q -p no:cacheprovider tests/test_analyze_api.py 2>&1 | ForEach-Object { Log "$_" }

Log "--- Full test suite:"
& $py -m pytest -o addopts= -q -p no:cacheprovider 2>&1 | ForEach-Object { Log "$_" }

# ---------------------------------------------------------------- 3. start the server
Log "--- Starting: uvicorn app.main:app --reload  (press Ctrl+C in this window to stop)"
& $py -m uvicorn app.main:app --reload --host 127.0.0.1 --port $port
