@echo off
REM Claude Weekly Rollup - scheduled task runner
REM After the rollup completes, push a heartbeat beacon to Homebase so Hestia can monitor.

cd /d C:\Dev\claude-memory-compiler
"C:\Users\Eric\.local\bin\uv.exe" run python scripts\weekly-rollup.py >> scripts\weekly-rollup.log 2>&1
set RC=%ERRORLEVEL%

REM Push beacon to Homebase (ignore failure - rollup success shouldn't depend on network)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz');" ^
  "$body = @{ name = 'ClaudeWeeklyRollup'; machine = $env:COMPUTERNAME; last_run = $ts; exit_code = %RC% } | ConvertTo-Json -Compress;" ^
  "$body | ssh homebase 'cat > /root/hestia/beacons/claude-weekly-rollup.json'" >> scripts\weekly-rollup.log 2>&1

exit /b %RC%
