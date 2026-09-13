param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$OutputPath = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")) ".codex\runtime")
)

$ErrorActionPreference = "Stop"
Push-Location $RepositoryRoot
try {
    python -m serverless.compiler.build_runtime --repository $RepositoryRoot --output $OutputPath
    if ($LASTEXITCODE -ne 0) { throw "Runtime compilation failed." }
} finally {
    Pop-Location
}
