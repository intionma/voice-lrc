@echo off
REM Only ASCII in this file. cmd.exe misreads non-ASCII batch files.
title trans-text setup
REM This file lives in scripts\. Work from the project root.
cd /d "%~dp0.."

REM ---- Say how big this is before spending the bandwidth ----
REM Nobody was told. The window sat on "Installing packages" for ten minutes
REM and there was no way to know whether that was 50 MB or 5 GB. This only
REM tells; it does not ask. Stopping to ask would be one more thing to answer
REM before anything works.
echo.
echo ============================================================
echo  What gets downloaded
echo ============================================================
echo.
echo   Python packages         about 500 MB   now
echo   CUDA libraries          about 1.5 GB   now, NVIDIA cards only
echo   Speech model            1.6 - 3 GB     later, on the first
echo                                          transcription, not now
echo.
echo   Total, NVIDIA card      about 5 GB
echo   Total, no NVIDIA card   about 3 GB
echo.
echo   The speech model is kept in %%APPDATA%%\trans-text\models and is
echo   downloaded once, not once per audio file.
echo.
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.11 -m venv .venv 2>nul
    if not exist ".venv\Scripts\python.exe" python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Could not create .venv
    echo Install Python 3.11 from python.org and run this again.
    echo Tick "Add python.exe to PATH" in the installer.
    echo.
    pause
    exit /b 1
)

set PY=.venv\Scripts\python.exe

REM ---- Is there an NVIDIA card? ----
REM Without one the CUDA libraries are 1.5 GB that never gets loaded:
REM transcription drops to CPU (asr.GpuUnavailable -> for_cpu) either way.
REM nvidia-smi ships with the driver, so it is the cheap check. Some machines
REM have the card but not the tool on PATH, so ask Windows for the card names
REM as well before giving up.
set HAS_NVIDIA=
where nvidia-smi >nul 2>&1
if not errorlevel 1 set HAS_NVIDIA=1

if not defined HAS_NVIDIA (
    powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" 2>nul | find /i "NVIDIA" >nul
    if not errorlevel 1 set HAS_NVIDIA=1
)

echo.
echo Installing packages. This takes several minutes.
echo.
"%PY%" -m pip install --upgrade pip

REM requirements.txt has faster-whisper AND pywebview.
REM The old version of this file did not install pywebview, so the
REM window could not open at all.
"%PY%" -m pip install -r app\requirements.txt

REM mutagen is only used by the old scripts\ pipeline.
"%PY%" -m pip install mutagen

if defined HAS_NVIDIA (
    echo.
    echo NVIDIA card found. Installing CUDA libraries, about 1.5 GB...
    echo.
    "%PY%" -m pip install --force-reinstall -r app\requirements-gpu.txt
) else (
    echo.
    echo No NVIDIA card found. Skipping the CUDA libraries, saving 1.5 GB.
    echo Transcription will run on the CPU: slower, but it works.
    echo If you do have an NVIDIA card, install its driver and run this again.
    echo.
)

echo.
echo ============================================================
echo Verifying
echo ============================================================
"%PY%" -c "import faster_whisper, av; print('Speech to text OK')"
"%PY%" -c "import webview; print('Window OK')"

REM Speech to text needs cuDNN too, not only cuBLAS. Checking only cuBLAS
REM printed "CUDA libraries OK" while transcription still failed at run time
REM with a confusing message. scripts/check_gpu.py lists what is required.
if defined HAS_NVIDIA (
    set CUDA_MISSING=
    if not exist ".venv\Lib\site-packages\nvidia\cublas\bin\cublas64_12.dll" set CUDA_MISSING=1
    if not exist ".venv\Lib\site-packages\nvidia\cudnn\bin\cudnn64_9.dll" set CUDA_MISSING=1
    if not exist ".venv\Lib\site-packages\nvidia\cudnn\bin\cudnn_ops64_9.dll" set CUDA_MISSING=1

    if defined CUDA_MISSING (
        echo.
        echo [WARNING] Some CUDA libraries were NOT installed.
        echo GPU will not work. Run 9_check_gpu.bat and send the output.
    ) else (
        echo CUDA libraries OK
    )
) else (
    echo CUDA libraries skipped - no NVIDIA card
)

echo.
echo ============================================================
echo Done. Now double click START.bat in the folder above.
echo ============================================================
echo.
pause
