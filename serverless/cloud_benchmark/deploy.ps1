param(
    [string]$Profile = 'darkest',
    [string]$Region = 'ca-central-1',
    [string]$Stack = 'soilie3d-benchmark-temporary',
    [Parameter(Mandatory = $true)]
    [ValidatePattern('@sha256:[0-9a-f]{64}$')]
    [string]$BaseImage,
    [int]$MemoryMB = 4096,
    [int]$Concurrency = 16,
    [string]$BuildContext = ''
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
# Never reuse an old build tree: Copy-Item would otherwise merge stale modules
# into the next image, or overwrite another task's retained build evidence.
$scratch = [IO.Path]::GetFullPath((Join-Path $repo '.codex'))
$context = if ($BuildContext) {
    if ([IO.Path]::IsPathRooted($BuildContext)) {
        [IO.Path]::GetFullPath($BuildContext)
    } else {
        [IO.Path]::GetFullPath((Join-Path $repo $BuildContext))
    }
} else {
    Join-Path $scratch ('cloud-benchmark-build-' + [Guid]::NewGuid().ToString('N'))
}
if (-not $context.StartsWith($scratch + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build context must be a dedicated directory inside this repository .codex folder'
}
for ($ancestor = [IO.DirectoryInfo]$context; $ancestor -and $ancestor.FullName -ne $repo; $ancestor = $ancestor.Parent) {
    if ($ancestor.Exists -and ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Build context must not traverse a junction or symbolic link'
    }
}
if (Test-Path -LiteralPath $context) {
    if (-not (Test-Path -LiteralPath $context -PathType Container) -or
        @(Get-ChildItem -LiteralPath $context -Force).Count) {
        throw 'Build context already contains files; choose a new task-owned directory'
    }
} else {
    New-Item -ItemType Directory -Path $context | Out-Null
}
$account = (aws sts get-caller-identity --profile $Profile --query Account --output text).Trim()
if ($LASTEXITCODE) { throw 'AWS authentication failed' }
$registry = "$account.dkr.ecr.$Region.amazonaws.com"
$repository = 'soilie3d-benchmark-temporary'
aws ecr describe-repositories --profile $Profile --region $Region --repository-names $repository 2>$null | Out-Null
if ($LASTEXITCODE) {
    aws ecr create-repository --profile $Profile --region $Region --repository-name $repository --image-tag-mutability IMMUTABLE | Out-Null
    if ($LASTEXITCODE) { throw 'Cannot create isolated benchmark repository' }
}
Copy-Item -LiteralPath (Join-Path $repo 'modules'),(Join-Path $repo 'serverless') -Destination $context -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repo 'package.json') -Destination $context
Copy-Item -LiteralPath (Join-Path $repo '.codex/runtime/model-provenance.json') -Destination (Join-Path $context 'provenance.json')
# A digest-pinned base can retain immutable meshes/source observations while
# the human-authored combination policy changes. Overlay only the five current
# catalogs, never a broad data tree or unrelated local source staging.
$catalogContext = Join-Path $context 'data'
New-Item -ItemType Directory -Path $catalogContext | Out-Null
$provenance = Get-Content -LiteralPath (Join-Path $context 'provenance.json') -Raw | ConvertFrom-Json
foreach ($catalog in @('working-combos-refined.csv', 'working-combos-bedroom.csv', 'working-combos-livingroom.csv', 'working-combos-kitchen.csv', 'working-combos-bathroom.csv')) {
    $relative = "data/$catalog"
    $expected = $provenance.files.PSObject.Properties[$relative].Value
    $source = Join-Path $repo $relative
    if (-not $expected -or (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected.sha256) {
        throw "Current catalog differs from compiled provenance: $relative"
    }
    Copy-Item -LiteralPath $source -Destination $catalogContext
    $copied = Join-Path $catalogContext $catalog
    if ((Get-FileHash -LiteralPath $copied -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected.sha256) {
        throw "Build-context catalog copy differs: $relative"
    }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Dockerfile') -Destination $context
# A live Windows/WSL checkout can contain interpreter-specific bytecode. Build
# from the verified source files, never stale local caches copied with folders.
Copy-Item -LiteralPath (Join-Path $repo '.dockerignore') -Destination $context
$linuxContext = (wsl -- wslpath -u $context.Replace('\','/')).Trim()
$tag = 'pilot-' + (Get-Date -Format 'yyyyMMddHHmmss')
$image = "$registry/${repository}:$tag"
aws ecr get-login-password --profile $Profile --region $Region | wsl -- docker login --username AWS --password-stdin $registry | Out-Null
if ($LASTEXITCODE) { throw 'ECR authentication failed' }
try {
    wsl -- docker build --platform linux/amd64 --provenance=false --build-arg "BASE_IMAGE=$BaseImage" -t $image $linuxContext
    if ($LASTEXITCODE) { throw 'Benchmark image build failed' }
    wsl -- docker push $image
    if ($LASTEXITCODE) { throw 'Benchmark image push failed' }
} finally {
    wsl -- docker logout $registry | Out-Null
}
$digest = (aws ecr describe-images --repository-name $repository --image-ids "imageTag=$tag" --profile $Profile --region $Region --query 'imageDetails[0].imageDigest' --output text).Trim()
if ($LASTEXITCODE) { throw 'Cannot resolve immutable benchmark image' }
$uri = "$registry/$repository@$digest"
aws cloudformation deploy --profile $Profile --region $Region --stack-name $Stack --template-file (Join-Path $PSScriptRoot 'template.yaml') --capabilities CAPABILITY_IAM --parameter-overrides "ImageUri=$uri" "MemoryMB=$MemoryMB" "Concurrency=$Concurrency" --tags Project=SOILIE-3D Purpose=TemporaryBenchmark
if ($LASTEXITCODE) { throw 'Temporary benchmark deployment failed' }
# A safety stop can have set concurrency to zero outside CloudFormation. An
# unchanged template parameter alone does not repair that deliberate drift.
aws lambda put-function-concurrency --profile $Profile --region $Region --function-name $Stack --reserved-concurrent-executions $Concurrency | Out-Null
if ($LASTEXITCODE) { throw 'Cannot restore the explicit benchmark concurrency' }
aws cloudformation describe-stacks --profile $Profile --region $Region --stack-name $Stack --query 'Stacks[0].Outputs' --output json
