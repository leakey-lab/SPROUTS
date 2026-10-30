@echo off
setlocal
REM Build the SPROUTS Windows bundle (dist\SPROUTS\SPROUTS.exe) with PyInstaller.
REM
REM Usage:   scripts\build.bat [/allow-missing-models]
REM Works from any folder: paths are derived from this file's location. Uses
REM %SPROUTS_BUILD_PYTHON% when set, else venv\Scripts\python.exe. Exits with a
REM non-zero code on any failure. Set SPROUTS_BUILD_NO_PAUSE=1 to skip the final pause.

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
if defined SPROUTS_BUILD_PYTHON (set "PY=%SPROUTS_BUILD_PYTHON%") else (set "PY=%ROOT%\venv\Scripts\python.exe")
set "ALLOW_MISSING="
if /i "%~1"=="/allow-missing-models" set "ALLOW_MISSING=1"

echo SPROUTS build (%ROOT%)

if not exist "%PY%" (
    echo Build failed: Python interpreter not found: %PY%
    goto :fail
)

set "MISSING="
if not exist "%ROOT%\checkpoints\mask_weights\best_mask_model_V5.pth" set "MISSING=%MISSING% checkpoints\mask_weights\best_mask_model_V5.pth"
if not exist "%ROOT%\checkpoints\skeletonizer\latest_net_G.pth" set "MISSING=%MISSING% checkpoints\skeletonizer\latest_net_G.pth"
if defined MISSING if not defined ALLOW_MISSING (
    echo Build failed: model weights missing:%MISSING%
    echo Place them under checkpoints\ ^(docs\MODELS.md^) or pass /allow-missing-models.
    goto :fail
)
if defined MISSING echo WARNING: building without model weights:%MISSING%

call "%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found; installing it...
    call "%PY%" -m pip install "pyinstaller>=6.16,<7"
    if errorlevel 1 (
        echo Build failed: could not install PyInstaller
        goto :fail
    )
)

echo Building ^(5-15 minutes on the first run^)...
call "%PY%" -m PyInstaller --noconfirm --clean --distpath "%ROOT%\dist" --workpath "%ROOT%\build" "%ROOT%\scripts\build_executable.spec"
if errorlevel 1 (
    echo Build failed: PyInstaller reported an error ^(see the messages above^).
    goto :fail
)

echo.
echo Build completed successfully: %ROOT%\dist\SPROUTS\SPROUTS.exe
echo To distribute, zip the whole dist\SPROUTS folder.
call :pause_if_interactive
exit /b 0

:fail
call :pause_if_interactive
exit /b 1

:pause_if_interactive
if not defined SPROUTS_BUILD_NO_PAUSE pause
exit /b 0
