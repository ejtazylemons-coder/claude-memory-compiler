# register-task.ps1
# Idempotently (re)registers the weekly claude-memory-compiler scheduled tasks.
# Run this on any new machine after cloning the repo — the scheduled tasks are
# NOT in git, so without this script a machine migration silently drops the
# weekly jobs (exactly what happened on the LOLA-001 laptop migration:
# weekly-lint.ps1 claimed it was registered but the task was gone, so lint +
# dream never fired until 2026-06-24).
#
#   powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1
#
# Registers TWO tasks:
#   1. ClaudeMemoryWeeklyLint  (Sun 8:00 AM) — weekly-lint.ps1: lint.py
#      --structural-only THEN dream.py --quiet --beacon. Free, no LLM.
#   2. ClaudeWeeklyCompile     (Sun 8:30 AM) — run-weekly-rollup.bat:
#      run-weekly-compile.py (rollup -> compile -> verify -> beacon). Spends
#      API $ under budget_guard caps. THIS task is the synthesis stage that
#      silently died 2026-04-14 — the orchestrator existed but was never
#      scheduled (Memory Spine Phase 0 fixes that). Staggered 30 min after
#      lint so the two don't contend for the venv / git.
# Both run only when the user is logged on (Interactive).

$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$repo = "C:\Dev\claude-memory-compiler"

$commonSettings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$commonPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# ── Task 1: weekly lint + dream (free) ──────────────────────────────────────
$lintScript = "$repo\scripts\weekly-lint.ps1"
if (-not (Test-Path $lintScript)) { Write-Error "weekly-lint.ps1 not found at $lintScript"; exit 1 }
$lintAction  = New-ScheduledTaskAction -Execute $ps -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File `"$lintScript`""
$lintTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8:00AM
Register-ScheduledTask -TaskName "ClaudeMemoryWeeklyLint" -Action $lintAction -Trigger $lintTrigger -Settings $commonSettings -Principal $commonPrincipal `
    -Description "Weekly knowledge-base lint + memory dream pass (claude-memory-compiler). Sundays 8AM. dream.py --beacon -> Homebase Ops worker claude-memory-dream." -Force | Out-Null

# ── Task 2: weekly compile/synthesis ($ — budget-guarded) ───────────────────
$compileBat = "$repo\scripts\run-weekly-rollup.bat"
if (-not (Test-Path $compileBat)) { Write-Error "run-weekly-rollup.bat not found at $compileBat"; exit 1 }
$compileAction  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$compileBat`""
$compileTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8:30AM
Register-ScheduledTask -TaskName "ClaudeWeeklyCompile" -Action $compileAction -Trigger $compileTrigger -Settings $commonSettings -Principal $commonPrincipal `
    -Description "Weekly memory synthesis (claude-memory-compiler): rollup -> compile -> verify -> beacon. Sundays 8:30AM. Beacon -> Homebase Ops worker claude-weekly-compile. Budget-guarded (15/mo cap). THIS is the synthesis stage that silently died 2026-04-14; Memory Spine Phase 0 wired it." -Force | Out-Null

Write-Output "Registered tasks:"
Get-ScheduledTask -TaskName "ClaudeMemoryWeeklyLint", "ClaudeWeeklyCompile" | Select-Object TaskName, State
