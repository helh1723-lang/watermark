param(
    [int]$ApiPort = 8765,
    [int]$WebPort = 5173,
    [switch]$OpenBrowser,
    [switch]$ReuseExisting
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$RuntimeDir = Join-Path $Root ".runtime"
$WebDir = Join-Path $Root "web"
$ApiPidFile = Join-Path $RuntimeDir "watermark_web_api.pid"
$WebPidFile = Join-Path $RuntimeDir "watermark_web_frontend.pid"
$ApiPortFile = Join-Path $RuntimeDir "watermark_web_api.port"
$WebPortFile = Join-Path $RuntimeDir "watermark_web_frontend.port"
$ApiLog = Join-Path $RuntimeDir "web_api.log"
$ApiErrLog = Join-Path $RuntimeDir "web_api.error.log"
$WebLog = Join-Path $RuntimeDir "web_frontend.log"
$WebErrLog = Join-Path $RuntimeDir "web_frontend.error.log"

function Write-Info($Message) {
    Write-Host "[Watermark Web] $Message"
}

function Resolve-Python {
    $candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root "venv\Scripts\python.exe"),
        "D:\Anaconda\python.exe",
        "D:\Anaconda3\python.exe",
        "C:\ProgramData\Anaconda3\python.exe",
        "python.exe",
        "python"
    )
    foreach ($candidate in $candidates) {
        try {
            if ($candidate -like "*\*") {
                if (Test-Path -LiteralPath $candidate) {
                    return (Resolve-Path -LiteralPath $candidate).Path
                }
            } else {
                $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
                if ($cmd) {
                    return $cmd.Source
                }
            }
        } catch {
        }
    }
    throw "No usable Python was found. Run scripts\setup_env.ps1 first."
}

function Get-ListeningPid($Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) {
            return [int]$conn.OwningProcess
        }
    } catch {
    }
    $lines = netstat -ano -p tcp | Select-String -Pattern "LISTENING"
    foreach ($line in $lines) {
        $text = [string]$line.Line
        if ($text -match "[:.]$Port\s+.*LISTENING\s+(\d+)$") {
            return [int]$Matches[1]
        }
    }
    return $null
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

function Open-Url($Url) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Url
    $psi.UseShellExecute = $true
    [System.Diagnostics.Process]::Start($psi) | Out-Null
}

function Start-DetachedCommand($Command, $WorkingDirectory) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = "/d /s /c `"$Command`""
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    return [System.Diagnostics.Process]::Start($psi)
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

$apiExisting = Get-ListeningPid $ApiPort
$webExisting = Get-ListeningPid $WebPort
if ($apiExisting -and $webExisting) {
    if ($ReuseExisting) {
        Write-Info "Already running."
        Write-Info "API: http://127.0.0.1:$ApiPort"
        Write-Info "App: http://127.0.0.1:$WebPort"
        if ($OpenBrowser) {
            Open-Url "http://127.0.0.1:$WebPort"
        }
        exit 0
    }
    Write-Info "Restarting existing web service to load the latest code..."
    Stop-ProcessTree $apiExisting
    Stop-ProcessTree $webExisting
    Start-Sleep -Seconds 1
    $apiExisting = $null
    $webExisting = $null
}

$python = Resolve-Python
$npm = (Get-Command "npm.cmd" -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath (Join-Path $WebDir "node_modules"))) {
    Write-Info "Installing web dependencies..."
    Push-Location $WebDir
    try {
        & $npm install
    } finally {
        Pop-Location
    }
}

if (-not $apiExisting) {
    $apiCommand = "call `"$python`" -m watermark_app.web_server --host 127.0.0.1 --port $ApiPort > `"$ApiLog`" 2> `"$ApiErrLog`""
    $apiProcess = Start-DetachedCommand $apiCommand $Root
    Set-Content -LiteralPath $ApiPidFile -Value $apiProcess.Id -Encoding ASCII
    Set-Content -LiteralPath $ApiPortFile -Value $ApiPort -Encoding ASCII
    Start-Sleep -Seconds 1
} else {
    Set-Content -LiteralPath $ApiPidFile -Value $apiExisting -Encoding ASCII
    Set-Content -LiteralPath $ApiPortFile -Value $ApiPort -Encoding ASCII
}

if (-not $webExisting) {
    $webCommand = "call `"$npm`" run dev -- --host 127.0.0.1 --port $WebPort > `"$WebLog`" 2> `"$WebErrLog`""
    $webProcess = Start-DetachedCommand $webCommand $WebDir
    Set-Content -LiteralPath $WebPidFile -Value $webProcess.Id -Encoding ASCII
    Set-Content -LiteralPath $WebPortFile -Value $WebPort -Encoding ASCII
} else {
    Set-Content -LiteralPath $WebPidFile -Value $webExisting -Encoding ASCII
    Set-Content -LiteralPath $WebPortFile -Value $WebPort -Encoding ASCII
}

Start-Sleep -Seconds 2
Write-Info "API:  http://127.0.0.1:$ApiPort"
Write-Info "App:  http://127.0.0.1:$WebPort"
Write-Info "Logs: $ApiLog ; $ApiErrLog ; $WebLog ; $WebErrLog"
if ($OpenBrowser) {
    Open-Url "http://127.0.0.1:$WebPort"
}
