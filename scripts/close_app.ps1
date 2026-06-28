$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$RuntimeDir = Join-Path $Root ".runtime"
$PidFile = Join-Path $RuntimeDir "watermark_app.pid"

function Write-Info($Message) {
    Write-Host "[Watermark] $Message"
}

function Get-RunningAppProcess($ProcessId) {
    if (-not $ProcessId) {
        return $null
    }
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            $commandLine = [string]$proc.CommandLine
            if ($commandLine -like "*launch_gui.py*") {
                return $proc
            }
        }
    } catch {
    }
    try {
        $proc2 = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($proc2 -and ($proc2.ProcessName -eq "python" -or $proc2.ProcessName -eq "pythonw")) {
            return $proc2
        }
    } catch {
    }
    return $null
}

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Info "No running app was found."
    exit 0
}

$pidText = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
if ($pidText -notmatch "^\d+$") {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Info "Invalid PID file was removed."
    exit 0
}

$running = Get-RunningAppProcess ([int]$pidText)
if (-not $running) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Info "The app was not running. PID file was removed."
    exit 0
}

Write-Info "Closing app, PID=$pidText"
Stop-Process -Id ([int]$pidText) -Force
Start-Sleep -Milliseconds 500
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Write-Info "Closed."
