# The weekly backup of the live system (ADR 0045, decision 11), run by a Windows scheduled task
# on the laptop; docs/deploy.md, Part 4, has the commands that register, run and remove it.
#
# It runs `trialpulse.live.backup backup --target neon` from the repository's folder and appends
# its output to data/logs/backup_<date>.log. A task that starts late (StartWhenAvailable, after
# the laptop slept) inside the daily job's window, 21:30 to 00:30 UTC, first waits until 00:30
# UTC. The backup prints counts, sizes and digests only, never a connection string.

$ErrorActionPreference = "Continue"  # 5.1 turns a redirected native stderr line into an error under Stop
Set-Location -LiteralPath (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path "data\logs" | Out-Null
$log = Join-Path "data\logs" ("backup_" + (Get-Date -Format "yyyyMMdd") + ".log")

$now = (Get-Date).ToUniversalTime()
$minutes = $now.Hour * 60 + $now.Minute
if ($minutes -ge (21 * 60 + 30) -or $minutes -lt 30) {
    $until = $now.Date.AddMinutes(30)
    if ($minutes -ge 30) { $until = $until.AddDays(1) }
    "$(Get-Date -Format o) inside the daily job's window: waiting until 00:30 UTC" | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds ([int]($until - $now).TotalSeconds)
}

"$(Get-Date -Format o) backup started" | Out-File -Append -Encoding utf8 $log
# cmd keeps the output as plain text (PowerShell 5.1 would write UTF-16 and wrap stderr)
cmd /c "uv run python -m trialpulse.live.backup backup --target neon >> `"$log`" 2>&1"
$code = $LASTEXITCODE
"$(Get-Date -Format o) backup ended with exit code $code" | Out-File -Append -Encoding utf8 $log
exit $code
