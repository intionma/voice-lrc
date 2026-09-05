@echo off
REM Only ASCII in this file. cmd.exe misreads non-ASCII batch files.
title trans-text
cd /d "%~dp0"

REM "cd /d" fails silently on a network path (\\server\share\...). Without
REM this check we would run from the wrong folder and complain that nothing
REM is installed, which tells the user nothing.
if not exist "app\main.py" goto wrong_folder

REM This is the only file you need. Double click it.
REM On the first run it installs what the program needs, then starts it.
REM After that it just starts.

if not exist ".venv\Scripts\python.exe" goto install
".venv\Scripts\python.exe" -c "import faster_whisper, webview" >nul 2>&1
if errorlevel 1 goto install
goto run

:install
echo.
echo ============================================================
echo  First run. Installing what the program needs.
echo  This takes several minutes. Leave this window open.
echo ============================================================
echo.
call "%~dp0scripts\setup.bat"

REM Check the same things as above, not only that .venv exists.
REM If pip stopped halfway the folder is there but the parts are not,
REM and starting anyway only shows a confusing Python error.
if not exist ".venv\Scripts\python.exe" goto install_failed
".venv\Scripts\python.exe" -c "import faster_whisper, webview" >nul 2>&1
if errorlevel 1 goto install_failed

goto run

:install_failed
echo.
echo [ERROR] Install did not finish. Copy the messages above and send them.
echo.
pause
exit /b 1

:run
REM Put an icon on the Desktop and in the Start menu, once ever.
REM
REM This cannot live in the :install branch. Anyone who already installed
REM would never pass through it, and they are exactly the people who have
REM no icon. The marker file makes it run once and never again, so normal
REM starts do not pay for a PowerShell launch.
REM
REM Failing here must not stop the program - it is a convenience, not a
REM requirement. That is why the marker is written either way.
REM The marker lives inside .venv, not in the root. The whole point is that
REM this folder shows one file. A marker sitting next to START.bat would be
REM the second one.
if exist ".venv\.desktop_done" goto skip_desktop
if not exist "scripts\setup_desktop.ps1" goto skip_desktop
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_desktop.ps1"
echo done > ".venv\.desktop_done"

:skip_desktop
REM Check that the program itself loads, while this window is still up.
REM An update can leave a broken .py behind, and once we go windowless
REM there is no console left to show a syntax error in.
".venv\Scripts\python.exe" -c "import app.ui.window" >nul 2>&1
if errorlevel 1 goto broken

REM Start it WITHOUT a console window and close this one.
REM python.exe is a console program, so waiting for it here kept a black
REM window open next to the app for as long as the app ran. Two windows
REM every time. pythonw.exe has no console.
REM
REM Nothing swallows errors: app\main.py writes _crash.log and shows a
REM message box if anything goes wrong.
if not exist ".venv\Scripts\pythonw.exe" goto run_with_console
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\main.py"
exit /b 0

:run_with_console
REM No pythonw.exe (unusual). Better a second window than no program.
".venv\Scripts\python.exe" "app\main.py"
if errorlevel 1 (
    echo.
    echo ============================================================
    echo The program stopped with an error.
    echo Copy everything above and send it.
    echo ============================================================
    echo.
    pause
)
exit /b 0

:wrong_folder
echo.
echo ============================================================
echo Could not open the program folder.
echo.
echo If this folder is on a network drive, copy it to your PC
echo and run START.bat from there.
echo ============================================================
echo.
pause
exit /b 1

:broken
echo.
echo ============================================================
echo The program files look broken. It cannot start.
echo This can happen if an update did not finish.
echo.
echo Details:
".venv\Scripts\python.exe" -c "import app.ui.window"
echo ============================================================
echo.
pause
exit /b 1
