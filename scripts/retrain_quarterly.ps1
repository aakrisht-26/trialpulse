# The quarterly retrain and its promotion gate (Section 14, ADR 0080), run by a Windows
# scheduled task on the laptop; docs/deploy.md, Part 5, has the commands that register, run and
# remove it.
#
# It runs `trialpulse.monitor.retrain --target neon` from the repository's folder and appends its
# output to data/logs/retrain_<date>.log. A task that starts late (StartWhenAvailable) inside the
# daily job's span, 21:30 to 08:00 UTC (GitHub starts the scheduled runs hours late), first waits
# until 08:00 UTC. The retrain prints counts and its decision only, never a connection string.

$ErrorActionPreference = "Continue"  # 5.1 turns a redirected native stderr line into an error under Stop
Set-Location -LiteralPath (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path "data\logs" | Out-Null
$log = Join-Path "data\logs" ("retrain_" + (Get-Date -Format "yyyyMMdd") + ".log")

$now = (Get-Date).ToUniversalTime()
$minutes = $now.Hour * 60 + $now.Minute
if ($minutes -ge (21 * 60 + 30) -or $minutes -lt (8 * 60)) {
    $until = $now.Date.AddHours(8)
    if ($minutes -ge (8 * 60)) { $until = $until.AddDays(1) }
    "$(Get-Date -Format o) inside the daily job's span: waiting until 08:00 UTC" | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds ([int]($until - $now).TotalSeconds)
}

"$(Get-Date -Format o) retrain started" | Out-File -Append -Encoding utf8 $log
# cmd keeps the output as plain text (PowerShell 5.1 would write UTF-16 and wrap stderr)
cmd /c "uv run python -m trialpulse.monitor.retrain --target neon >> `"$log`" 2>&1"
$code = $LASTEXITCODE
"$(Get-Date -Format o) retrain ended with exit code $code" | Out-File -Append -Encoding utf8 $log
exit $code
