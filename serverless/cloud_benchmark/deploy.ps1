param(
    [string]$Profile = 'darkest',
    [string]$Region = 'ca-central-1',
    [string]$Stack = 'soilie3d-benchmark-temporary',
    [string]$BaseDigest = 'sha256:c4782694ebeb5ff2f65f418ab2a4c82184b5f70ba9f8dda121181cbc44758b2a',
    [int]$MemoryMB = 4096,
    [int]$Concurrency = 4
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$account = (aws sts get-caller-identity --profile $Profile --query Account --output text).Trim()
if ($LASTEXITCODE) { throw 'AWS authentication failed' }
$registry = "$account.dkr.ecr.$Region.amazonaws.com"
$repository = 'soilie3d-benchmark-temporary'
aws ecr describe-repositories --profile $Profile --region $Region --repository-names $repository 2>$null | Out-Null
if ($LASTEXITCODE) {
    aws ecr create-repository --profile $Profile --region $Region --repository-name $repository --image-tag-mutability IMMUTABLE | Out-Null
    if ($LASTEXITCODE) { throw 'Cannot create isolated benchmark repository' }
}
$context = Join-Path $repo '.codex/cloud-benchmark-build'
New-Item -ItemType Directory -Force -Path $context | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'modules'),(Join-Path $repo 'serverless') -Destination $context -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repo 'package.json') -Destination $context
Copy-Item -LiteralPath (Join-Path $repo '.codex/runtime/v4-provenance.json') -Destination (Join-Path $context 'provenance.json')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Dockerfile') -Destination $context
$linuxContext = (wsl -- wslpath -u $context.Replace('\','/')).Trim()
$tag = 'pilot-' + (Get-Date -Format 'yyyyMMddHHmmss')
$image = "$registry/${repository}:$tag"
aws ecr get-login-password --profile $Profile --region $Region | wsl -- docker login --username AWS --password-stdin $registry | Out-Null
if ($LASTEXITCODE) { throw 'ECR authentication failed' }
try {
    wsl -- docker build --platform linux/amd64 --provenance=false --build-arg "BASE_IMAGE=$registry/soilie3d-renderer@$BaseDigest" -t $image $linuxContext
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
