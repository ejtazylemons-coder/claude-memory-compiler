@echo off
REM Claude Weekly Compile - scheduled task runner
REM Runs the full weekly pipeline: rollup → compile → verify → push beacon.
REM
REM Replaces the prior model where this bat ran weekly-rollup.py alone and
REM compile.py was unwired (so the wiki at concepts/ + connections/ + qa/
REM stopped growing 2026-04-14).
REM
REM Safety rails live in scripts/budget_guard.py — combined monthly hard cap,
REM silent-zero detector, .compiler-disabled.flag auto-trip + Telegram alert.

REM Force UTF-8 stdout/stderr so Unicode chars (arrows, em-dash) in prints don't
REM crash under Windows cp1252 console encoding. Silent failure 2026-04-19
REM traced to UnicodeEncodeError on -> (right-arrow) in main() print.
set PYTHONIOENCODING=utf-8

cd /d C:\Dev\claude-memory-compiler

REM Use venv python directly (mirrors the 2026-05-02 fix to session-end.py +
REM the 2026-05-04 fix to pre-compact.py — no more hardcoded user paths).
".venv\Scripts\python.exe" scripts\run-weekly-compile.py >> scripts\weekly-rollup.log 2>&1
set RC=%ERRORLEVEL%

REM Beacon push is handled by run-weekly-compile.py with the rich shape
REM (exit_code, monthly_spent, outputs_written, disabled, etc.).
REM No second beacon write here.

exit /b %RC%
