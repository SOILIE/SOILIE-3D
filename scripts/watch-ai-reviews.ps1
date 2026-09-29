param(
    [string]$Root = (Join-Path $PSScriptRoot '../.codex/benchmark/soilie-platform-grid-final/review-functional-use-v3-full'),
    [switch]$Once
)
$ErrorActionPreference = 'Stop'
$progressFile = Join-Path $Root 'progress.json'
$dimensionNames = [ordered]@{
    relationships = 'Object relationships'
    orientation = 'Orientation'
    access = 'Access and circulation'
    proportions = 'Relative size'
    room_function = 'Room function'
}

function Read-ReviewProgress {
    # Permit the collector's atomic rename while this monitor holds a handle.
    # Get-Content does not reliably grant delete-sharing on Windows.
    $sharing = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $stream = [IO.File]::Open($progressFile, [IO.FileMode]::Open, [IO.FileAccess]::Read, $sharing)
    try {
        $reader = [IO.StreamReader]::new($stream)
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
}

function Format-ReviewEta($Seconds) {
    if ($null -eq $Seconds) { return 'calculating' }
    return ([TimeSpan]::FromSeconds($Seconds)).ToString('d\d\ hh\h\ mm\m')
}

try {
    while ($true) {
        if (Test-Path -LiteralPath $progressFile) {
            try { $state = Read-ReviewProgress } catch { if ($Once) { throw }; Start-Sleep -Seconds 1; continue }
            $barId = 0
            foreach ($key in $dimensionNames.Keys) {
                $barId++
                $group = @($state.dimensions | Where-Object { $_.key -eq $key }) | Select-Object -First 1
                $percent = if ($group -and $group.expected) { [Math]::Min(100, 100 * $group.completed / $group.expected) } else { 0 }
                $status = if ($group) {
                    "$($group.completed) / $($group.expected) judgments | $($group.active) active | ETA $(Format-ReviewEta $group.etaSeconds)"
                } else { 'Waiting for validated dimension counts' }
                $status += " | $($state.state)"
                if ($state.state -eq 'needs_attention') { $status += " | $($state.error)" }
                if ($state.state -eq 'running' -and $state.updatedAt -and ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($state.updatedAt)).TotalSeconds -gt 30) {
                    $status += ' | heartbeat stale; check collector log'
                }
                Write-Progress -Id $barId -Activity $dimensionNames[$key] -Status $status -PercentComplete $percent `
                    -CurrentOperation "Overall: $($state.completed)/$($state.expected) | $($state.workers) workers | ETA $(Format-ReviewEta $state.etaSeconds)"
            }
            if ($state.state -eq 'complete') {
                Write-Host "Complete: $($state.completed) judgments. Results saved in $Root"
                break
            }
        } else {
            $barId = 0
            foreach ($label in $dimensionNames.Values) {
                Write-Progress -Id (++$barId) -Activity $label -Status 'Waiting for collector heartbeat' -PercentComplete 0
            }
        }
        if ($Once) { break }
        Start-Sleep -Seconds 1
    }
} finally {
    for ($barId = 1; $barId -le 5; $barId++) { Write-Progress -Id $barId -Activity 'AI reviews' -Completed }
}
