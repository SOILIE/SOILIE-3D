[CmdletBinding()]
param(
    [string]$Profile = "darkest",
    [string]$Region = "ca-central-1",
    [string]$StackName = "soilie3d-github-deployment"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$template = Join-Path $repoRoot "serverless\infra\deployment-role.yaml"
aws cloudformation deploy `
    --profile $Profile `
    --region $Region `
    --stack-name $StackName `
    --template-file $template `
    --capabilities CAPABILITY_NAMED_IAM `
    --no-fail-on-empty-changeset
if ($LASTEXITCODE -ne 0) { throw "SOILIE-3D GitHub deployment roles could not be provisioned." }
aws cloudformation describe-stacks `
    --profile $Profile `
    --region $Region `
    --stack-name $StackName `
    --query "Stacks[0].Outputs" `
    --output table
