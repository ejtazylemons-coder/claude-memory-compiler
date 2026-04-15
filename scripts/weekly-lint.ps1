# weekly-lint.ps1
# Runs structural health checks on the knowledge base and logs results.
# Registered as a weekly Task Scheduler job.
# Runs every Sunday at 8 AM. No LLM calls — structural checks only (free).
# Results are reported during next sync up, not via Telegram.

$ErrorActionPreference = "SilentlyContinue"

$RepoDir  = "C:\Dev\claude-memory-compiler"
$LogFile  = "$RepoDir\scripts\lint-cron.log"
$UvExe    = "$env:USERPROFILE\.local\bin\uv.exe"

$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
Add-Content $LogFile "[$Timestamp] Starting weekly lint..."

$Result = & $UvExe run --directory $RepoDir python "$RepoDir\scripts\lint.py" --structural-only 2>&1
Add-Content $LogFile $Result

$Errors = 0; $Warnings = 0; $Suggestions = 0
if ($Result -match 'Results: (\d+) errors, (\d+) warnings, (\d+) suggestions') {
    $Errors      = [int]$matches[1]
    $Warnings    = [int]$matches[2]
    $Suggestions = [int]$matches[3]
}

Add-Content $LogFile "[$Timestamp] Lint complete: $Errors errors, $Warnings warnings, $Suggestions suggestions"
