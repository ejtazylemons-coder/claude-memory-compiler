@echo off
:: Weekly lint cron for claude-memory-compiler
:: Runs structural checks only (no LLM cost) — append output to lint-cron.log

echo [%date% %time%] === Lint run started === >> "%~dp0lint-cron.log" 2>&1
REM Resolve uv robustly (2026-08-16): old hardcoded C:\Users\Eric path was dead
REM (previous machine). Same resolver order as weekly-lint.ps1.
set "UV=uv.exe"
where uv >nul 2>&1
if errorlevel 1 (
  set "UV=%USERPROFILE%\AppData\Local\Programs\Python\Python312\Scripts\uv.exe"
  if not exist "%UV%" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not exist "%UV%" set "UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
)
"%UV%" run --directory "C:\Dev\claude-memory-compiler" python "C:\Dev\claude-memory-compiler\scripts\lint.py" --structural-only >> "%~dp0lint-cron.log" 2>&1
echo [%date% %time%] === Lint run complete === >> "%~dp0lint-cron.log" 2>&1
