param(
    [string]$Profile = "darkest",
    [string]$Region = "ca-central-1",
    [string]$Bucket = "soilie3d-data",
    [string]$Prefix = "files/"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$workingDirectory = Join-Path $repoRoot ".codex"
$jsonPath = Join-Path $workingDirectory "files-index.json"
$gzipPath = "$jsonPath.gz"
New-Item -ItemType Directory -Force -Path $workingDirectory | Out-Null

try {
    $raw = aws s3api list-objects-v2 --profile $Profile --region $Region --bucket $Bucket --prefix $Prefix --query "Contents[].Key" --output json
    if ($LASTEXITCODE -ne 0) { throw "Could not enumerate published research files." }
    $keys = @($raw | ConvertFrom-Json) | Where-Object { $_ -and -not $_.EndsWith("/") } | Sort-Object -Unique
    $document = [ordered]@{
        schemaVersion = 1
        generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        keys = $keys
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($jsonPath, $document, [System.Text.UTF8Encoding]::new($false))

    $inputStream = [System.IO.File]::OpenRead($jsonPath)
    $outputStream = [System.IO.File]::Create($gzipPath)
    try {
        $gzipStream = [System.IO.Compression.GZipStream]::new($outputStream, [System.IO.Compression.CompressionLevel]::SmallestSize)
        try { $inputStream.CopyTo($gzipStream) } finally { $gzipStream.Dispose() }
    } finally {
        $inputStream.Dispose()
        $outputStream.Dispose()
    }

    aws s3 cp $gzipPath "s3://$Bucket/catalog/files-index.json" --profile $Profile --region $Region --content-type "application/json; charset=utf-8" --content-encoding gzip --cache-control "no-cache" --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "Could not publish the research file index." }
    Write-Output "Published $($keys.Count) keys to s3://$Bucket/catalog/files-index.json"
} finally {
    Remove-Item -LiteralPath $jsonPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $gzipPath -Force -ErrorAction SilentlyContinue
}
