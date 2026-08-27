@echo off
REM Monthly State-of-the-State Synthesis - scheduled task runner
REM Fires on the 1st of each month at 06:00 ET (covers prior month).
REM After run, push beacon to Homebase so Daily Monitor can detect death.

REM Force UTF-8 stdout/stderr (same fix as weekly-rollup, prevents cp1252 crashes).
set PYTHONIOENCODING=utf-8

cd /d C:\Dev\claude-memory-compiler

REM Resolve uv robustly (2026-08-16, exit-9009 fix): the old hardcoded
REM ~\.local\bin\uv.exe vanished; uv is pip-installed under Python312\Scripts.
REM Same resolver order as weekly-lint.ps1: PATH first, then known locations.
set "UV=uv.exe"
where uv >nul 2>&1
if errorlevel 1 (
  set "UV=%USERPROFILE%\AppData\Local\Programs\Python\Python312\Scripts\uv.exe"
  if not exist "%UV%" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not exist "%UV%" set "UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
)

"%UV%" run python scripts\monthly-state-synthesis.py >> scripts\monthly-state-synthesis.log 2>&1
set RC=%ERRORLEVEL%

REM Push beacon to Homebase (failure ignored - synthesis success shouldn't depend on network)
REM -WindowStyle Hidden per feedback_windows_hooks_hidden.md
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ^
  "$OutputEncoding = New-Object System.Text.UTF8Encoding($false);" ^
  "$ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz');" ^
  "$body = @{ name = 'monthly-state-synthesis'; machine = $env:COMPUTERNAME; last_run = $ts; exit_code = %RC%; summary = 'monthly state synthesis run'; version = '1.0.0' } | ConvertTo-Json -Compress;" ^
  "$body | ssh homebase 'cat > /root/hestia/beacons/monthly-state-synthesis.json'" >> scripts\monthly-state-synthesis.log 2>&1

exit /b %RC%
