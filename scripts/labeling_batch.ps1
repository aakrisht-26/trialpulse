# One daily labeling batch of Step 6 (CLAUDE.md Section 11): label the next texts of the
# 10,000-text LLM sample until the provider's daily limit stops the run. The run is
# resumable and keeps every label it got. Output goes to data\logs\labeling\, one file per
# day (data\ is not committed).
#
# Run by the Windows scheduled task "TrialPulse labeling batch" (see docs/progress.md), or
# by hand:
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\labeling_batch.ps1
#
# Exit codes are the labeler's: 0 when the run finished (or the sample is complete),
# 4 when the daily limit stopped it (the usual end of a batch), 2 for a refusal such as a
# missing key, which a batch the next day will not fix.

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logDir = Join-Path $repo "data\logs\labeling"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("labeling_" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function Write-Log([string]$line) {
    Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $line) -Encoding UTF8
}

# A scheduled task may not see the PATH of an interactive shell: find uv either way.
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe"
}
if (-not (Test-Path $uv)) {
    Write-Log "stopped: uv was not found"
    exit 2
}

$env:PYTHONIOENCODING = "utf-8"
Write-Log "start (uv: $uv)"
$status = & $uv run python -m trialpulse.nlp.llm_labeler --status 2>$null | Out-String
if ($status -match '"remaining":\s*0\b') {
    Write-Log "the sample is complete: nothing to label"
    exit 0
}
# cmd appends the bytes as they come, in order, without PowerShell's own encoding.
cmd /c "`"$uv`" run python -m trialpulse.nlp.llm_labeler --sample 10000 >> `"$log`" 2>&1"
$code = $LASTEXITCODE
Write-Log "end, exit code $code"
exit $code
