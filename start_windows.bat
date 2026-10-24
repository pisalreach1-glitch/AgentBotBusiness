@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
if not exist .env (
  copy .env.example .env >nul
  echo Fill TELEGRAM_TOKEN and GEMINI_API_KEY in .env, then start again.
  notepad .env
  exit /b 1
)
.venv\Scripts\python.exe -m app.main
pause
