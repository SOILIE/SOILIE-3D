param(
    [string]$Root = (Join-Path $PSScriptRoot '../.codex/benchmark/soilie-platform-grid-final/review-functional-use-v3-full')
)
$ErrorActionPreference = 'Stop'
$progressFile = Join-Path $Root 'progress.json'
try {
    while ($true) {
        if (Test-Path -LiteralPath $progressFile) {
            try { $state = Get-Content -Raw -LiteralPath $progressFile | ConvertFrom-Json } catch { Start-Sleep -Seconds 1; continue }
            $percent = [Math]::Min(100, 100 * $state.completed / [Math]::Max(1, $state.expected))
            $eta = if ($null -eq $state.etaSeconds) { 'calculating' } else { ([TimeSpan]::FromSeconds($state.etaSeconds)).ToString('d\d\ hh\h\ mm\m') }
            $status = "$($state.completed) / $($state.expected) judgments | $($state.state) | ETA $eta"
            if ($state.state -eq 'needs_attention') { $status += " | $($state.error)" }
            if ($state.state -eq 'running' -and $state.updatedAt -and ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($state.updatedAt)).TotalSeconds -gt 30) { $status += ' | heartbeat stale; collector may have stopped' }
            Write-Progress -Id 1 -Activity 'SOILIE comparison: 480 pairs x 5 dimensions x 2 presentations' -Status $status -PercentComplete $percent
            if ($state.state -eq 'complete') {
                Write-Progress -Id 1 -Activity 'AI reviews' -Completed
                Write-Host "Complete: $($state.completed) judgments. Results saved in $Root"
                break
            }
        } else {
            Write-Progress -Id 1 -Activity 'AI reviews' -Status 'Waiting for collector heartbeat' -PercentComplete 0
        }
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Progress -Id 1 -Activity 'AI reviews' -Completed
}
