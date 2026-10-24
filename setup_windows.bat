@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto install
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install
:usepython
python -m venv .venv
if errorlevel 1 goto failed
:install
.venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 goto oldpython
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist .env copy .env.example .env >nul
echo Setup completed. Fill TELEGRAM_TOKEN and GEMINI_API_KEY in .env.
echo GEMINI_MODEL is prefilled. Change it if your AI Studio project uses a different model.
echo Then double-click start_windows.bat.
notepad .env
pause
exit /b 0
:oldpython
echo Python 3.11 or newer is required. Install it, recreate .venv and run setup again.
pause
exit /b 1
:failed
echo Setup failed. Check Python and your internet connection. Read README_KH.md.
pause
exit /b 1
