param(
    [switch]$UseVenv
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$WebDir = Join-Path $Root "web"

function Write-Info($Message) {
    Write-Host "[Watermark Setup] $Message"
}

function Resolve-BasePython {
    $candidates = @(
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
    throw "Python was not found. Install Python 3.10+ with Tkinter, or install Anaconda."
}

Write-Info "Preparing Python dependencies..."
$python = Resolve-BasePython
if ($UseVenv) {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $python -m venv (Join-Path $Root ".venv")
    }
    $python = $venvPython
}

& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $Root "requirements.txt")

Write-Info "Checking optional converters..."
$soffice = Get-Command "soffice" -ErrorAction SilentlyContinue
if (-not $soffice) {
    Write-Info "LibreOffice was not found. DOC/DOCX strong conversion needs LibreOffice in PATH."
}
$ffmpeg = Get-Command "ffmpeg" -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Info "ffmpeg was not found. Video watermarking still works, but output may not keep original audio."
}

Write-Info "Preparing web dependencies..."
$npm = (Get-Command "npm.cmd" -ErrorAction Stop).Source
Push-Location $WebDir
try {
    & $npm install
    & $npm run build
} finally {
    Pop-Location
}

Write-Info "Running Python probe..."
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ScriptDir "start_app.ps1") -ProbeOnly

Write-Info "Environment is ready."
