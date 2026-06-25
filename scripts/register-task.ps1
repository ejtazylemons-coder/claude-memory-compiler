# register-task.ps1
# Idempotently (re)registers the weekly knowledge-base lint + memory dream job
# as a Windows Task Scheduler task. Run this on any new machine after cloning
# the repo — the scheduled task is NOT in git, so without this script a machine
# migration silently drops the weekly job (exactly what happened on the LOLA-001
# laptop migration: weekly-lint.ps1 claimed it was registered but the task was
# gone, so lint + dream never fired until 2026-06-24).
#
#   powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1
#
# Runs weekly-lint.ps1 (which runs lint.py --structural-only THEN dream.py
# --quiet --beacon) every Sunday at 8 AM, only when the user is logged on.

$TaskName = "ClaudeMemoryWeeklyLint"
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$script = "C:\Dev\claude-memory-compiler\scripts\weekly-lint.ps1"

if (-not (Test-Path $script)) { Write-Error "weekly-lint.ps1 not found at $script"; exit 1 }

$action    = New-ScheduledTaskAction -Execute $ps -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File `"$script`""
$trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8:00AM
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Weekly knowledge-base lint + memory dream pass (claude-memory-compiler). Sundays 8AM. dream.py --beacon -> Homebase Ops worker claude-memory-dream." -Force | Out-Null

Write-Output "Registered '$TaskName':"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
