# weekly-lint.ps1
# Runs structural health checks on the knowledge base and sends a Telegram summary.
# Registered as a weekly Task Scheduler job by install-memory-compiler.ps1.
# Runs every Sunday at 9 PM. No LLM calls — structural checks only (free).

$ErrorActionPreference = "SilentlyContinue"

$RepoDir  = "C:\Dev\claude-memory-compiler"
$EnvFile  = "$RepoDir\.env"
$LogFile  = "$RepoDir\scripts\lint-cron.log"
$UvExe    = "$env:USERPROFILE\.local\bin\uv.exe"

# Load .env
$BotToken = ""
$ChatId   = ""
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^TELEGRAM_BOT_TOKEN=(.+)$') { $BotToken = $matches[1] }
        if ($_ -match '^TELEGRAM_CHAT_ID=(.+)$')   { $ChatId   = $matches[1] }
    }
}

function Send-Telegram($Text) {
    if (-not $BotToken -or -not $ChatId) { return }
    $Body = @{ chat_id = $ChatId; text = $Text }
    Invoke-RestMethod -Uri "https://api.telegram.org/bot$BotToken/sendMessage" `
        -Method Post -Body $Body -ErrorAction SilentlyContinue | Out-Null
}

# Run lint
$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
Add-Content $LogFile "[$Timestamp] Starting weekly lint..."

$Result = & $UvExe run --directory $RepoDir python "$RepoDir\scripts\lint.py" --structural-only 2>&1
Add-Content $LogFile $Result

# Parse counts from output
$Errors      = 0
$Warnings    = 0
$Suggestions = 0
if ($Result -match 'Results: (\d+) errors, (\d+) warnings, (\d+) suggestions') {
    $Errors      = [int]$matches[1]
    $Warnings    = [int]$matches[2]
    $Suggestions = [int]$matches[3]
}

# Send Telegram summary
$Date = Get-Date -Format "yyyy-MM-dd"
if ($Errors -gt 0) {
    Send-Telegram "Knowledge base lint ($Date): $Errors errors, $Warnings warnings — check reports/lint-$Date.md"
} elseif ($Warnings -gt 0) {
    Send-Telegram "Knowledge base lint ($Date): clean ($Warnings warnings, $Suggestions suggestions)"
} else {
    Send-Telegram "Knowledge base lint ($Date): all clear"
}

Add-Content $LogFile "[$Timestamp] Lint complete: $Errors errors, $Warnings warnings, $Suggestions suggestions"
