param(
    [string]$Profile = "darkest",
    [string]$Region = "ca-central-1",
    [string]$StackName = "soilie3d-engine"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$accountId = aws sts get-caller-identity --profile $Profile --query Account --output text
if ($LASTEXITCODE -ne 0) { throw "AWS credentials are unavailable." }
docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop must be running before deployment." }

& (Join-Path $PSScriptRoot "build-runtime.ps1") -RepositoryRoot $repoRoot
$registry = "$accountId.dkr.ecr.$Region.amazonaws.com"
$repositories = @("soilie3d-api", "soilie3d-renderer")
foreach ($repository in $repositories) {
    aws ecr describe-repositories --repository-names $repository --profile $Profile --region $Region 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        aws ecr create-repository --repository-name $repository --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true --profile $Profile --region $Region | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not create ECR repository $repository." }
    }
    $lifecycle = '{"rules":[{"rulePriority":1,"description":"Keep the three newest deployment images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}'
    aws ecr put-lifecycle-policy --repository-name $repository --lifecycle-policy-text $lifecycle --profile $Profile --region $Region | Out-Null
}

aws ecr get-login-password --profile $Profile --region $Region | docker login --username AWS --password-stdin $registry | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ECR login failed." }

$tag = Get-Date -Format "yyyyMMddHHmmss"
$apiUri = "$registry/soilie3d-api`:$tag"
$rendererUri = "$registry/soilie3d-renderer`:$tag"
Push-Location $repoRoot
try {
    docker build --platform linux/amd64 --provenance=false -f serverless/api/Dockerfile -t $apiUri .
    if ($LASTEXITCODE -ne 0) { throw "API image build failed." }
    docker build --platform linux/amd64 --provenance=false -f serverless/renderer/Dockerfile -t $rendererUri .
    if ($LASTEXITCODE -ne 0) { throw "Renderer image build failed." }
    # Docker's image-size field can report compressed content with the
    # containerd store. A saved archive expands every layer, making its byte
    # size a conservative proxy for Lambda's uncompressed image quota.
    $imageArchive = Join-Path $repoRoot ".codex\renderer-image-size-check.tar"
    docker image save --output $imageArchive $rendererUri
    if ($LASTEXITCODE -ne 0) { throw "Could not measure the renderer image." }
    try {
        $rendererBytes = (Get-Item -LiteralPath $imageArchive).Length
        if ($rendererBytes -ge 10200547328) { throw "Renderer image exceeds the 9.5 GiB deployment guard." }
    } finally {
        Remove-Item -LiteralPath $imageArchive -Force -ErrorAction SilentlyContinue
    }
    docker push $apiUri
    if ($LASTEXITCODE -ne 0) { throw "API image push failed." }
    docker push $rendererUri
    if ($LASTEXITCODE -ne 0) { throw "Renderer image push failed." }
} finally {
    Pop-Location
}

$parameters = @("ApiImageUri=$apiUri", "RendererImageUri=$rendererUri")
aws cloudformation describe-stacks --stack-name $StackName --profile $Profile --region $Region 2>$null | Out-Null
$stackExists = $LASTEXITCODE -eq 0
if (-not $stackExists) {
    $secretBytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($secretBytes)
    $visitorSecret = [Convert]::ToHexString($secretBytes).ToLowerInvariant()
    $parameters += "VisitorHmacSecret=$visitorSecret"
}
$template = Join-Path $repoRoot "serverless\infra\template.yaml"
aws cloudformation deploy --template-file $template --stack-name $StackName --capabilities CAPABILITY_NAMED_IAM --parameter-overrides $parameters --tags Project=SOILIE-3D --profile $Profile --region $Region
if ($LASTEXITCODE -ne 0) { throw "CloudFormation deployment failed." }

$endpoint = aws cloudformation describe-stacks --stack-name $StackName --profile $Profile --region $Region --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text
Write-Output "API_ENDPOINT=$endpoint"
Write-Output "API_IMAGE=$apiUri"
Write-Output "RENDERER_IMAGE=$rendererUri"
