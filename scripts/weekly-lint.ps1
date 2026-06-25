# weekly-lint.ps1
# Runs structural health checks on the knowledge base and logs results.
# Registered as a weekly Task Scheduler job.
# Runs every Sunday at 8 AM. No LLM calls — structural checks only (free).
# Results are reported during next sync up, not via Telegram.

$ErrorActionPreference = "SilentlyContinue"

$RepoDir  = "C:\Dev\claude-memory-compiler"
$LogFile  = "$RepoDir\scripts\lint-cron.log"

# Resolve uv robustly — the old hardcoded ~/.local/bin/uv.exe does NOT exist on
# the LOLA-001 laptop (uv is pip-installed under Python312\Scripts), which made
# this whole job silently no-op after the migration. Try PATH, then known
# install locations, before giving up.
$UvExe = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $UvExe) {
    foreach ($c in @(
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\Scripts\uv.exe",
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe"
    )) { if (Test-Path $c) { $UvExe = $c; break } }
}

$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
Add-Content $LogFile "[$Timestamp] Starting weekly lint... (uv=$UvExe)"

if ($UvExe) {
    $Result = & $UvExe run --directory $RepoDir python "$RepoDir\scripts\lint.py" --structural-only 2>&1
} else {
    $Result = "ERROR: uv.exe not found - lint skipped this run"
}
Add-Content $LogFile $Result

$Errors = 0; $Warnings = 0; $Suggestions = 0
if ($Result -match 'Results: (\d+) errors, (\d+) warnings, (\d+) suggestions') {
    $Errors      = [int]$matches[1]
    $Warnings    = [int]$matches[2]
    $Suggestions = [int]$matches[3]
}

Add-Content $LogFile "[$Timestamp] Lint complete: $Errors errors, $Warnings warnings, $Suggestions suggestions"

# Memory "dreaming" pass — structural consolidation/staleness audit over the
# ~/.claude memory store (MEMORY.md + feedback_*.md). Free, non-destructive.
# Run via plain python (dream.py is stdlib-only) so memory hygiene does NOT
# depend on uv being present — resilient to exactly the drift that broke lint.
# --beacon pushes health to Homebase for the Ops worker `claude-memory-dream`.
Add-Content $LogFile "[$Timestamp] Starting memory dream..."
$DreamResult = & python "$RepoDir\scripts\dream.py" --quiet --beacon 2>&1
Add-Content $LogFile $DreamResult
Add-Content $LogFile "[$Timestamp] Memory dream complete."
