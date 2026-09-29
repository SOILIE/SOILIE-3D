param(
    [string]$Root = (Join-Path $PSScriptRoot '../.codex/benchmark/soilie-platform-grid-final/review-functional-use-v3-full'),
    [switch]$Once,
    [ValidateSet('Auto', 'Always', 'Never')][string]$Color = 'Auto',
    [ValidateRange(0, 240)][int]$Width = 0
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
$dimensionColors = @(81, 141, 114, 221, 209)
$escape = [string][char]27
$oldEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$interactive = -not [Console]::IsOutputRedirected
$virtualTerminal = $Host.UI.SupportsVirtualTerminal -or $env:WT_SESSION -or $env:TERM_PROGRAM -eq 'vscode' -or $env:TERM -match 'xterm|ansi'
$script:useColor = $Color -eq 'Always' -or ($Color -eq 'Auto' -and $interactive -and $virtualTerminal)
$alternateScreen = $interactive -and $virtualTerminal -and -not $Once

function Read-ReviewProgress {
    # Grant delete-sharing so the collector can replace its snapshot atomically.
    $sharing = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $stream = [IO.File]::Open($progressFile, [IO.FileMode]::Open, [IO.FileAccess]::Read, $sharing)
    try {
        $reader = [IO.StreamReader]::new($stream)
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
}

function Format-ReviewEta($Seconds) {
    if ($null -eq $Seconds) { return 'estimating' }
    $span = [TimeSpan]::FromSeconds([Math]::Max(0, $Seconds))
    if ($span.TotalDays -ge 1) { return '{0}d {1:00}h {2:00}m' -f $span.Days, $span.Hours, $span.Minutes }
    if ($span.TotalHours -ge 1) { return '{0}h {1:00}m' -f [int][Math]::Floor($span.TotalHours), $span.Minutes }
    return '{0}m {1:00}s' -f [int][Math]::Floor($span.TotalMinutes), $span.Seconds
}

function Paint([string]$Text, [int]$Shade = 250, [switch]$Bold) {
    if (-not $script:useColor) { return $Text }
    $weight = if ($Bold) { '1;' } else { '' }
    return "$escape[${weight}38;5;${Shade}m$Text$escape[0m"
}

function Shorten([string]$Text, [int]$Limit) {
    if ($Limit -le 0) { return '' }
    if ($Text.Length -le $Limit) { return $Text }
    if ($Limit -lt 4) { return $Text.Substring(0, $Limit) }
    return $Text.Substring(0, $Limit - 3) + '...'
}

function Join-Columns([string]$Left, [string]$Right, [int]$Columns, [int]$Shade = 250) {
    $Right = Shorten $Right ([Math]::Max(1, $Columns - 4))
    $Left = Shorten $Left ([Math]::Max(1, $Columns - $Right.Length - 1))
    return (Paint $Left $Shade -Bold) + (' ' * [Math]::Max(1, $Columns - $Left.Length - $Right.Length)) + (Paint $Right 252)
}

function New-ReviewBar([double]$Completed, [double]$Expected, [int]$Columns, [int]$Shade) {
    $length = [Math]::Max(1, $Columns - 2)
    $fraction = if ($Expected -gt 0) { [Math]::Max(0.0, [Math]::Min(1.0, $Completed / $Expected)) } else { 0.0 }
    # Eighth-cell blocks keep small but nonzero progress visible and smooth.
    $units = [int][Math]::Floor($fraction * $length * 8)
    if ($fraction -gt 0 -and $units -eq 0) { $units = 1 }
    $whole = [int][Math]::Floor($units / 8)
    $part = $units % 8
    $fill = ([string][char]0x2588) * $whole
    if ($part) { $fill += [string][char](0x2590 - $part) }
    $empty = ([string][char]0x2591) * ($length - $whole - [int]($part -gt 0))
    return (Paint '[' 240) + (Paint $fill $Shade) + (Paint $empty 238) + (Paint ']' 240)
}

function New-ReviewDashboard($State, [int]$Columns, [string]$ReadWarning, [int]$Rows = 0) {
    $lines = [Collections.Generic.List[string]]::new()
    $status = if ($State) { ([string]$State.state).ToUpperInvariant().Replace('_', ' ') } else { 'WAITING FOR COLLECTOR' }
    $stale = $State -and $State.state -eq 'running' -and $State.updatedAt -and
        ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($State.updatedAt)).TotalSeconds -gt 30
    $statusShade = if ($State.state -eq 'needs_attention' -or $stale -or $ReadWarning) { 215 } elseif ($State.state -eq 'complete') { 114 } else { 81 }
    if ($stale) { $status = 'HEARTBEAT STALE' }
    if ($ReadWarning) { $status = 'WAITING FOR FRESH STATUS' }
    $lines.Add((Paint (Shorten 'SOILIE-3D  /  AI REVIEW CAMPAIGN' $Columns) 255 -Bold))
    $workers = if ($State) { '{0}/{1} workers active' -f [int]$State.active, $State.workers } else { 'Monitor only' }
    $lines.Add((Join-Columns $status $workers $Columns $statusShade))
    $lines.Add('')
    $total = if ($State) { [int]$State.expected } else { 4800 }
    $done = if ($State) { [int]$State.completed } else { 0 }
    $percent = 100 * $done / [Math]::Max(1, $total)
    $counter = '{0:N0} / {1:N0}   {2,5:0.0}%' -f $done, $total, $percent
    $lines.Add((Join-Columns 'OVERALL' $counter $Columns 81))
    $lines.Add((New-ReviewBar $done $total $Columns 81))
    $rate = if ($State.elapsedSeconds -gt 0) {
        $new = ($State.dimensions | Measure-Object -Property sessionCompleted -Sum).Sum
        '{0:0.0} reviews/hour' -f ($new * 3600 / $State.elapsedSeconds)
    } else { 'Starting up' }
    $lines.Add((Join-Columns ('ETA ' + (Format-ReviewEta $State.etaSeconds)) $rate $Columns 245))
    $lines.Add((Paint (([string][char]0x2500) * $Columns) 238))
    $index = 0
    foreach ($key in $dimensionNames.Keys) {
        $group = @($State.dimensions | Where-Object { $_.key -eq $key }) | Select-Object -First 1
        $count = if ($group) { [int]$group.completed } else { 0 }
        $expected = if ($group) { [int]$group.expected } else { 960 }
        $percent = 100 * $count / [Math]::Max(1, $expected)
        $counter = '{0:N0} / {1:N0}   {2,5:0.0}%' -f $count, $expected, $percent
        $shade = $dimensionColors[$index++]
        $lines.Add((Join-Columns $dimensionNames[$key] $counter $Columns $shade))
        $detail = '{0} active | ETA {1}' -f [int]$group.active, (Format-ReviewEta $group.etaSeconds)
        # Narrow terminals retain real bars; counts and percentages stay above.
        $detailWidth = [Math]::Max(0, [Math]::Min(28, $Columns - 14))
        $detail = (Shorten $detail $detailWidth).PadRight($detailWidth)
        $barWidth = $Columns - $detailWidth - 2
        $lines.Add((New-ReviewBar $count $expected $barWidth $shade) + '  ' + (Paint $detail 245))
    }
    $lines.Add((Paint (([string][char]0x2500) * $Columns) 238))
    $footer = if ($State.state -eq 'needs_attention') {
        'ATTENTION: ' + $State.error
    } elseif ($stale) {
        'Status is stale. Check the collector log; counts may be outdated.'
    } elseif ($ReadWarning) { $ReadWarning } else {
        'Refresh: 1s | Ctrl+C closes this monitor, not the reviews.'
    }
    $lines.Add((Paint (Shorten $footer $Columns) $statusShade))
    $lines.Add((Paint (Shorten 'ETAs are provisional and settle as more judgments finish.' $Columns) 243))
    if ($Rows -gt 0 -and $Rows -lt $lines.Count) {
        # VS Code's terminal panel is often short: retain all six bars on screen.
        $compact = [Collections.Generic.List[string]]::new()
        $compact.Add((Paint (Shorten ("SOILIE-3D / $status / $workers") $Columns) $statusShade -Bold))
        $groups = @([pscustomobject]@{label='OVERALL';completed=$done;expected=$total})
        foreach ($key in $dimensionNames.Keys) {
            $group = @($State.dimensions | Where-Object { $_.key -eq $key }) | Select-Object -First 1
            $groups += [pscustomobject]@{label=$dimensionNames[$key];completed=[int]$group.completed;expected=960}
        }
        $index = 0
        foreach ($group in $groups) {
            $counter = ('{0}/{1} {2:0.0}%' -f $group.completed, $group.expected,
                (100 * $group.completed / [Math]::Max(1,$group.expected))).PadLeft(16)
            $labelWidth = [Math]::Max(1, [Math]::Min(21, $Columns - $counter.Length - 9))
            $label = (Shorten $group.label $labelWidth).PadRight($labelWidth)
            $barWidth = $Columns - $labelWidth - $counter.Length - 2
            $shade = if ($index -eq 0) { 81 } else { $dimensionColors[$index - 1] }
            $compact.Add((Paint $label $shade -Bold) + ' ' +
                (New-ReviewBar $group.completed $group.expected $barWidth $shade) + ' ' + (Paint $counter 252))
            $index++
        }
        $compact.Add((Paint (Shorten ("ETA $(Format-ReviewEta $State.etaSeconds) | Ctrl+C: close monitor") $Columns) 245))
        if ($Rows -ge 9 -and ($stale -or $ReadWarning -or $State.state -eq 'needs_attention')) {
            $compact.Add((Paint (Shorten $footer $Columns) $statusShade))
        }
        return $compact.ToArray()
    }
    return $lines.ToArray()
}

$state = $null
$legacyTop = 0
$oldControlKeyMode = $null
$stopMonitor = $false
try {
    if ($interactive -and -not $Once) {
        # ConPTY can deliver Ctrl+C as input rather than a process signal.
        # Handle it explicitly so the cursor and original screen are restored.
        $oldControlKeyMode = [Console]::TreatControlCAsInput
        [Console]::TreatControlCAsInput = $true
    }
    if ($alternateScreen) {
        # Dedicated screen avoids scroll spam and restores the original terminal.
        [Console]::Write("$escape[?1049h$escape[?25l")
    } elseif ($interactive -and -not $Once) {
        $legacyTop = [Console]::CursorTop
    }
    while ($true) {
        $readWarning = ''
        if (Test-Path -LiteralPath $progressFile) {
            try { $state = Read-ReviewProgress } catch {
                $readWarning = 'Status temporarily unreadable; retrying. Reviews are unaffected.'
            }
        } elseif ($state) { $readWarning = 'Waiting for the collector status file to return.' }
        $windowWidth = if ($Width) { $Width } elseif ($interactive) { [Console]::WindowWidth } else { 96 }
        $columns = [Math]::Max(24, [Math]::Min(116, $windowWidth - 4))
        $windowHeight = if ($interactive) { [Console]::WindowHeight } else { 0 }
        $lines = @(New-ReviewDashboard $state $columns $readWarning $windowHeight | ForEach-Object { '  ' + $_ })
        $frame = $lines -join [Environment]::NewLine
        if ($alternateScreen) {
            [Console]::Write("$escape[H" + ($lines -join "$escape[K$([Environment]::NewLine)") + "$escape[K$escape[J")
        } elseif ($interactive -and -not $Once) {
            # Old Windows hosts without ANSI still get real in-place block bars.
            $height = $lines.Count
            if ($legacyTop + $height -ge [Console]::BufferHeight) { $legacyTop = 0 }
            [Console]::SetCursorPosition(0, $legacyTop)
            foreach ($line in $lines) { [Console]::WriteLine($line.PadRight($columns + 2)) }
        } else { [Console]::WriteLine($frame) }
        if ($state.state -eq 'complete' -or $Once) { break }
        for ($tick = 0; $tick -lt 10; $tick++) {
            if ($interactive -and [Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.KeyChar -eq [char]3) { $stopMonitor = $true; break }
            }
            Start-Sleep -Milliseconds 100
        }
        if ($stopMonitor) { break }
    }
} finally {
    if ($alternateScreen) { [Console]::Write("$escape[0m$escape[?25h$escape[?1049l") }
    if ($null -ne $oldControlKeyMode) { [Console]::TreatControlCAsInput = $oldControlKeyMode }
    [Console]::OutputEncoding = $oldEncoding
}
if ($state.state -eq 'complete' -and -not $Once) {
    Write-Host "Complete: $($state.completed) judgments. Results saved in $Root"
}
