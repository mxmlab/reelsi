# SPDX-License-Identifier: AGPL-3.0-or-later
# Reelsi - Windows installation.
#
#   powershell -ExecutionPolicy Bypass -File reelsi\install.ps1
#
# This script does NOT install ffmpeg or Python: they are system-level
# dependencies and pulling them silently from the internet is not appropriate.
# It tells you what is missing and how to install it.
#
# The main reason this script exists: correct torch build. If you install torch
# with a plain `pip install torch`, you get the CPU build, and all GPU
# computation becomes orders of magnitude slower - silently, with no errors.

param(
    [switch]$Cpu,          # force CPU torch build (skip GPU detection)
    [switch]$NoOptional    # skip optional dependencies
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($msg)  { Write-Host "  $msg" }
function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Bad($msg)  { Write-Host "  ERROR: $msg" -ForegroundColor Red }

function Resolve-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $exe = (& py -3.10 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $exe -and $exe.Trim() -ne "") {
                return $exe.Trim()
            }
        } catch {
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        try {
            $exe = (& python -c "import sys; print(sys.executable) if sys.version_info[:2] == (3, 10) else sys.exit(1)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $exe -and $exe.Trim() -ne "") {
                return $exe.Trim()
            }
        } catch {
        }
    }

    throw "Python 3.10 not found. Install it: winget install Python.Python.3.10 - and if it is already installed, make sure it is registered with the py launcher."
}

Step "Checking environment"

try {
    $py = Resolve-Python
} catch {
    Bad $_.Exception.Message
    exit 1
}
Say "Python 3.10: $py"

if (-not $env:REELSI_NO_VENV) {
    $inVenv = $false
    try {
        & $py -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $inVenv = $true }
    } catch {
    }
    if (-not $inVenv) {
        $venvOk = $false
        try {
            & $py -c "import venv, ensurepip" 2>$null
            if ($LASTEXITCODE -eq 0) { $venvOk = $true }
        } catch {
        }
        if (-not $venvOk) {
            Bad "Python venv or ensurepip module is missing. Install Python with venv support or run with `$env:REELSI_NO_VENV=1"
            exit 1
        }
        $venvDir = Join-Path $here ".venv"
        Say "Creating virtual environment in $venvDir"
        & $py -m venv $venvDir
        $venvPy = Join-Path $venvDir "Scripts\python.exe"
        if (-not (Test-Path $venvPy)) {
            $venvPy = Join-Path $venvDir "bin\python.exe"
        }
        if (Test-Path $venvPy) {
            $py = $venvPy
        }
    }
}

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Say "ffmpeg found"
} else {
    Bad "ffmpeg not found in PATH - no audio or rendering without it."
    Say "Install: winget install Gyan.FFmpeg  (then open a new terminal window)"
    exit 1
}

Step "Installing torch"

# GPU detection via nvidia-smi: the only reliable indicator before torch
# is installed.
$cuda = $false
if (-not $Cpu) {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try { nvidia-smi | Out-Null; $cuda = $true } catch { $cuda = $false }
    }
}

if ($cuda) {
    Say "NVIDIA detected - installing CUDA 12.1 build"
    & $py -m pip install torch==2.5.1 torchaudio==2.5.1 torchvision==0.20.1 `
        --index-url https://download.pytorch.org/whl/cu121
} else {
    if ($Cpu) { Say "CPU build (forced via -Cpu flag)" }
    else {
        Say "NVIDIA not detected - installing CPU build."
        Say "If you have an AMD GPU: there is a separate ROCm build, see docs/PLATFORMS.md"
    }
    & $py -m pip install torch torchaudio torchvision
}

Step "Installing dependencies"
& $py -m pip install -r (Join-Path $here "requirements.txt")

if (-not $NoOptional) {
    Step "Optional dependencies (skip with -NoOptional)"
    Say "Each one disables a single feature; errors here are not fatal."
    # Intentionally without -ErrorAction Stop: gigaam pulls from git and fails
    # for users without git in PATH. That is not a reason to abort installation.
    $ErrorActionPreference = "Continue"
    & $py -m pip install -r (Join-Path $here "requirements-optional.txt")
    $ErrorActionPreference = "Stop"
}

Step "Verification"
& $py (Join-Path $here "doctor.py")
# bootstrap переехал в пакет core/: из корня он запускается как модуль, а не по пути
# к файлу — иначе `core.paths` не найдётся и личные файлы заведутся не там.
Push-Location $here
try { & $py -m core.bootstrap } finally { Pop-Location }

Write-Host "`nRun:  & `"$py`" reelsi\webui.py" -ForegroundColor Green
