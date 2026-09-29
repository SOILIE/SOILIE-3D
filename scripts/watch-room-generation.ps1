param(
    [string]$Root = (Join-Path $PSScriptRoot '../.codex/arbutus-inventory'),
    [switch]$Once
)
$ErrorActionPreference = 'Stop'
$escape = [string][char]27
$interactive = -not [Console]::IsOutputRedirected
$ansi = $interactive -and ($Host.UI.SupportsVirtualTerminal -or $env:WT_SESSION -or $env:TERM_PROGRAM -eq 'vscode')
$originalEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
function Paint([string]$text, [int]$color) {
    if ($ansi) { return "$escape[38;5;${color}m$text$escape[0m" }
    return $text
}
function Bar([int]$done, [int]$total, [int]$width, [int]$color) {
    $length = [Math]::Max(8, $width - 2)
    # Force the floating-point overload; integer Math.Min rounds a fraction
    # to 0 or 1 and misleadingly renders an empty/full bar midway through.
    $units = [int][Math]::Floor($length * 8 * [Math]::Min(1.0, [double]$done / [Math]::Max(1.0, [double]$total)))
    $whole = [int][Math]::Floor($units / 8)
    $part = $units % 8
    $fill = ([string][char]0x2588) * $whole
    if ($part) { $fill += [string][char](0x2590 - $part) }
    return '[' + (Paint $fill $color) + (Paint (([string][char]0x2591) * ($length - $whole - [int]($part -gt 0))) 238) + ']'
}
function ETA($seconds) {
    if ($null -eq $seconds) { return 'estimating' }
    $span = [TimeSpan]::FromSeconds($seconds)
    return ('{0}h {1:00}m' -f [int][Math]::Floor($span.TotalHours), $span.Minutes)
}
$keyMode = $null
try {
    if ($interactive -and -not $Once) {
        $keyMode = [Console]::TreatControlCAsInput
        [Console]::TreatControlCAsInput = $true
        if ($ansi) { [Console]::Write("$escape[?1049h$escape[?25l") }
    }
    while ($true) {
        $width = if ($interactive) { [Math]::Max(26, [Math]::Min(100, [Console]::WindowWidth - 3)) } else { 86 }
        $state = $null
        $path = Join-Path $Root 'progress.json'
        if (Test-Path -LiteralPath $path) {
            try {
                $stream = [IO.File]::Open($path, 'Open', 'Read', ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
                $reader = [IO.StreamReader]::new($stream)
                try { $state = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose(); $stream.Dispose() }
            } catch { }
        }
        $lines = [Collections.Generic.List[string]]::new()
        $lines.Add((Paint 'SOILIE-3D / ROOM GENERATION' 255))
        if (-not $state) { $lines.Add('Waiting for the local relay status...') } else {
            $lines.Add(('OVERALL   {0}/{1}  {2:0.0}%' -f $state.completed, $state.expected, (100*$state.completed/[Math]::Max(1,$state.expected))))
            $lines.Add((Bar $state.completed $state.expected $width 81))
            $lines.Add(('Overall ETA: {0}' -f (ETA $state.etaSeconds)))
            foreach ($group in $state.groups) {
                $color = if ($group.key -eq 'infinigen') { 221 } else { 141 }
                $lines.Add('')
                $lines.Add((Paint ('{0}   {1}/{2}   ETA {3}' -f $group.key.ToUpper(), $group.completed, $group.expected, (ETA $group.etaSeconds)) $color))
                $lines.Add((Bar $group.completed $group.expected $width $color))
                $lines.Add(('Bedrooms {0}/120 | Living rooms {1}/120' -f $group.rooms.bedroom, $group.rooms.living_room))
                $lines.Add(('{0} active | {1} need attention | {2} new files delivered | {3} retained pairs' -f $group.active, $group.failed, $group.delivered, $group.retained))
            }
            $lines.Add(('OpenAI accounted: US${0:0.00} / US${1:0.00} cap' -f $state.apiAccountedUsd, $state.apiCapUsd))
            if ($state.warning) { $lines.Add((Paint $state.warning 209)) }
            if (([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($state.updatedAt)).TotalSeconds -gt 90) {
                $lines.Add((Paint 'Relay heartbeat is stale; counts may be outdated.' 209))
            }
        }
        $lines.Add('Refresh 1s | Ctrl+C closes this monitor, not the workers.')
        if ($ansi -and -not $Once) {
            [Console]::Write("$escape[H" + ($lines -join "$escape[K`n") + "$escape[K$escape[J")
        } else {
            if ($interactive -and -not $Once) { Clear-Host }
            [Console]::WriteLine(($lines -join [Environment]::NewLine))
        }
        if ($Once -or $state.state -eq 'complete') { break }
        $stop = $false
        for ($tick=0; $tick -lt 10; $tick++) {
            if ($interactive -and [Console]::KeyAvailable -and [Console]::ReadKey($true).KeyChar -eq [char]3) { $stop=$true; break }
            Start-Sleep -Milliseconds 100
        }
        if ($stop) { break }
    }
} finally {
    if ($ansi -and -not $Once) { [Console]::Write("$escape[0m$escape[?25h$escape[?1049l") }
    if ($null -ne $keyMode) { [Console]::TreatControlCAsInput = $keyMode }
    [Console]::OutputEncoding = $originalEncoding
}
