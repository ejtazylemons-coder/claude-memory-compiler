@echo off
REM Claude Weekly Rollup - scheduled task runner
REM After the rollup completes, push a heartbeat beacon to Homebase so Hestia can monitor.

REM Force UTF-8 stdout/stderr so Unicode chars (arrows, em-dash) in prints don't
REM crash under Windows cp1252 console encoding. Silent failure 2026-04-19
REM traced to UnicodeEncodeError on → (right-arrow) in main() print.
set PYTHONIOENCODING=utf-8

cd /d C:\Dev\claude-memory-compiler
"%USERPROFILE%\.local\bin\uv.exe" run python scripts\weekly-rollup.py >> scripts\weekly-rollup.log 2>&1
set RC=%ERRORLEVEL%

REM Push beacon to Homebase (ignore failure - rollup success shouldn't depend on network)
REM -WindowStyle Hidden per feedback_windows_hooks_hidden.md — this child PS call
REM was popping a visible window during the 2026-04-26 9:00 PM Saturday run.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ^
  "$ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz');" ^
  "$body = @{ name = 'ClaudeWeeklyRollup'; machine = $env:COMPUTERNAME; last_run = $ts; exit_code = %RC% } | ConvertTo-Json -Compress;" ^
  "$body | ssh homebase 'cat > /root/hestia/beacons/claude-weekly-rollup.json'" >> scripts\weekly-rollup.log 2>&1

exit /b %RC%
