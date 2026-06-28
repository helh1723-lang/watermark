param(
    [switch]$NoWaitCheck,
    [switch]$ProbeOnly
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$RuntimeDir = Join-Path $Root ".runtime"
$PidFile = Join-Path $RuntimeDir "watermark_app.pid"
$LogFile = Join-Path $RuntimeDir "watermark_app.log"
$ErrorLogFile = Join-Path $RuntimeDir "watermark_app.error.log"
$GuiScript = Join-Path $ScriptDir "launch_gui.py"

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

function Resolve-Python {
    $condaPython = $null
    if ($env:CONDA_PREFIX) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
    }
    $candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root "venv\Scripts\python.exe"),
        $condaPython,
        "D:\Anaconda\python.exe",
        "D:\Anaconda3\python.exe",
        "C:\ProgramData\Anaconda3\python.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        "python.exe",
        "python",
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
    )
    $probeLog = Join-Path $RuntimeDir "python_probe.log"
    Set-Content -LiteralPath $probeLog -Value "Python probe started $(Get-Date -Format o)" -Encoding UTF8

    foreach ($candidate in $candidates) {
        try {
            $resolved = $null
            if (-not $candidate) {
                continue
            }
            if ($candidate -like "*\*") {
                if (Test-Path -LiteralPath $candidate) {
                    $resolved = (Resolve-Path -LiteralPath $candidate).Path
                }
            } else {
                $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
                if ($cmd) {
                    $resolved = $cmd.Source
                }
            }
            if ($resolved) {
                $probe = "import warnings; warnings.simplefilter('ignore'); import tkinter as tk; r=tk.Tk(); r.withdraw(); r.destroy(); import PIL, numpy, pypdf, docx, reportlab, cv2, pywt, skimage"
                Add-Content -LiteralPath $probeLog -Value "Checking: $resolved" -Encoding UTF8
                & $resolved -c $probe *>> $probeLog
                if ($LASTEXITCODE -eq 0) {
                    Add-Content -LiteralPath $probeLog -Value "OK: $resolved" -Encoding UTF8
                    return $resolved
                }
                Add-Content -LiteralPath $probeLog -Value "FAILED exit=${LASTEXITCODE}: $resolved" -Encoding UTF8
            }
        } catch {
            Add-Content -LiteralPath $probeLog -Value "ERROR $candidate : $($_.Exception.Message)" -Encoding UTF8
        }
    }
    throw "No usable Python was found. Install Python with Tkinter plus Pillow, numpy, pypdf, python-docx, reportlab, opencv-python, PyWavelets, and scikit-image. Probe log: $probeLog"
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

if (-not (Test-Path -LiteralPath $GuiScript)) {
    throw "Missing GUI script: $GuiScript"
}

if (Test-Path -LiteralPath $PidFile) {
    $oldPidText = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($oldPidText -match "^\d+$") {
        $running = Get-RunningAppProcess ([int]$oldPidText)
        if ($running) {
            Write-Info "The app is already running, PID=$oldPidText"
            exit 0
        }
    }
}

$python = Resolve-Python
if ($ProbeOnly) {
    Write-Info "Python probe passed: $python"
    Write-Info "Probe log: $(Join-Path $RuntimeDir "python_probe.log")"
    exit 0
}

$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (Test-Path -LiteralPath $pythonw) {
    $launcher = $pythonw
} else {
    $launcher = $python
}
$arguments = "`"$GuiScript`""
Write-Info "Starting GUI..."
Write-Info "Python: $launcher"

$process = Start-Process `
    -FilePath $launcher `
    -ArgumentList $arguments `
    -WorkingDirectory $Root `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII

if (-not $NoWaitCheck) {
    Start-Sleep -Seconds 2
    $running = Get-RunningAppProcess $process.Id
    if (-not $running) {
        Write-Info "Startup failed because the app exited immediately."
        exit 1
    }
}

Write-Info "Started successfully, PID=$($process.Id)"
