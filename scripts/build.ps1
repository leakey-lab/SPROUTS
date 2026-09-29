<#
.SYNOPSIS
    Build the SPROUTS Windows bundle (dist\SPROUTS\SPROUTS.exe) with PyInstaller.

.DESCRIPTION
    Runs scripts\build_executable.spec with the project's venv interpreter. Works from any
    folder: every path is derived from this script's location, and the build output goes to
    <repo>\build and <repo>\dist. Exits with a non-zero code on any failure.

    The model weights (docs\MODELS.md) must be present under checkpoints\ unless
    -AllowMissingModels is given: a bundle without them cannot generate masks or skeletons.

.PARAMETER Python
    Interpreter to build with. Default: $env:SPROUTS_BUILD_PYTHON, else venv\Scripts\python.exe.

.PARAMETER AllowMissingModels
    Build even if the model weights are missing (for example a CPU smoke build in CI).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = $env:SPROUTS_BUILD_PYTHON,
    [switch]$AllowMissingModels
)

$Root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $Root 'venv\Scripts\python.exe' }

function Stop-Build([string]$Message) {
    Write-Host ""
    Write-Host "Build failed: $Message" -ForegroundColor Red
    exit 1
}

Write-Host "SPROUTS build ($Root)" -ForegroundColor Cyan

if (-not (Test-Path $Python)) {
    Stop-Build "Python interpreter not found: $Python (create the venv, see README 'Installation')"
}

$models = @(
    'checkpoints\mask_weights\best_mask_model_V5.pth',
    'checkpoints\skeletonizer\latest_net_G.pth'
)
$missing = @($models | Where-Object { -not (Test-Path (Join-Path $Root $_)) })
if ($missing.Count -gt 0) {
    if (-not $AllowMissingModels) {
        Stop-Build ("model weights missing: $($missing -join ', '). Place them under " +
            "checkpoints\ (docs\MODELS.md) or pass -AllowMissingModels.")
    }
    Write-Host "WARNING: building without model weights: $($missing -join ', ')" -ForegroundColor Yellow
}

& $Python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not found; installing it..." -ForegroundColor Yellow
    & $Python -m pip install "pyinstaller>=6.16,<7"
    if ($LASTEXITCODE -ne 0) { Stop-Build "could not install PyInstaller" }
}

Write-Host "Building (5-15 minutes on the first run)..." -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $Root 'dist') `
    --workpath (Join-Path $Root 'build') `
    (Join-Path $Root 'scripts\build_executable.spec')
if ($LASTEXITCODE -ne 0) {
    Stop-Build "PyInstaller exited with code $LASTEXITCODE (see the messages above)"
}

Write-Host ""
Write-Host "Build completed successfully: $(Join-Path $Root 'dist\SPROUTS\SPROUTS.exe')" -ForegroundColor Green
Write-Host "To distribute, zip the whole dist\SPROUTS folder." -ForegroundColor Green
exit 0
