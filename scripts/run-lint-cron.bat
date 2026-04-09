@echo off
:: Weekly lint cron for claude-memory-compiler
:: Runs structural checks only (no LLM cost) — append output to lint-cron.log

echo [%date% %time%] === Lint run started === >> "%~dp0lint-cron.log" 2>&1
"C:\Users\Eric\.local\bin\uv.exe" run --directory "C:\Dev\claude-memory-compiler" python "C:\Dev\claude-memory-compiler\scripts\lint.py" --structural-only >> "%~dp0lint-cron.log" 2>&1
echo [%date% %time%] === Lint run complete === >> "%~dp0lint-cron.log" 2>&1
