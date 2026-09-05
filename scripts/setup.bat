@echo off
REM Only ASCII in this file. cmd.exe misreads non-ASCII batch files.
title trans-text setup
REM This file lives in scripts\. Work from the project root.
cd /d "%~dp0.."

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

echo.
echo Installing CUDA libraries for GPU...
echo.
"%PY%" -m pip install --force-reinstall nvidia-cublas-cu12 nvidia-cudnn-cu12

echo.
echo ============================================================
echo Verifying
echo ============================================================
"%PY%" -c "import faster_whisper, av; print('Speech to text OK')"
"%PY%" -c "import webview; print('Window OK')"

REM Speech to text needs cuDNN too, not only cuBLAS. Checking only cuBLAS
REM printed "CUDA libraries OK" while transcription still failed at run time
REM with a confusing message. scripts/check_gpu.py lists what is required.
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

echo.
echo ============================================================
echo Done. Now double click START.bat in the folder above.
echo ============================================================
echo.
pause
