param(
    [int]$ApiPort = 8765,
    [int]$WebPort = 5173
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$RuntimeDir = Join-Path $Root ".runtime"
$PidFiles = @(
    (Join-Path $RuntimeDir "watermark_web_api.pid"),
    (Join-Path $RuntimeDir "watermark_web_frontend.pid")
)
$PortFiles = @(
    (Join-Path $RuntimeDir "watermark_web_api.port"),
    (Join-Path $RuntimeDir "watermark_web_frontend.port")
)

function Write-Info($Message) {
    Write-Host "[Watermark Web] $Message"
}

function Stop-ProcessTree($ProcessId) {
    if (-not $ProcessId) {
        return
    }
    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-ProcessTree ([int]$child.ProcessId)
        }
    } catch {
    }
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
}

function Get-ListeningPids($Port) {
    $pids = @()
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($item in $conn) {
            $pids += [int]$item.OwningProcess
        }
    } catch {
    }
    if ($pids.Count -eq 0) {
        $lines = netstat -ano -p tcp | Select-String -Pattern "LISTENING"
        foreach ($line in $lines) {
            $text = [string]$line.Line
            if ($text -match "[:.]$Port\s+.*LISTENING\s+(\d+)$") {
                $pids += [int]$Matches[1]
            }
        }
    }
    return $pids | Select-Object -Unique
}

$stopped = 0
foreach ($file in $PidFiles) {
    if (Test-Path -LiteralPath $file) {
        $text = (Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue).Trim()
        if ($text -match "^\d+$") {
            Stop-ProcessTree ([int]$text)
            $stopped += 1
        }
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }
}

foreach ($port in @($ApiPort, $WebPort)) {
    foreach ($listenPid in Get-ListeningPids $port) {
        Stop-ProcessTree ([int]$listenPid)
        $stopped += 1
    }
}

foreach ($file in $PortFiles) {
    Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
}

if ($stopped -eq 0) {
    Write-Info "No running web service was found."
} else {
    Write-Info "Closed web service."
}
