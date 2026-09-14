#!/usr/bin/env bash
set -euo pipefail

region="${AWS_REGION:-ca-central-1}"
stack="${SOILIE_ENGINE_STACK:-soilie3d-engine}"
account_id="$(aws sts get-caller-identity --query Account --output text)"
registry="${account_id}.dkr.ecr.${region}.amazonaws.com"
tag="${GITHUB_SHA:?GITHUB_SHA is required}"
api_uri="${registry}/soilie3d-api:${tag}"
renderer_uri="${registry}/soilie3d-renderer:${tag}"
model_version="$(node -p "require('./package.json').version")"
asset_sha="$(sha256sum serverless/runtime-assets.json | cut -d' ' -f1)"

aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"

build_and_push() {
  local repository="$1"
  local dockerfile="$2"
  local uri="$3"
  if aws ecr describe-images --region "$region" --repository-name "$repository" \
      --image-ids "imageTag=${tag}" >/dev/null 2>&1; then
    echo "Reusing immutable ${repository}:${tag}"
    return
  fi
  if [[ "$repository" == "soilie3d-renderer" ]] && docker image inspect soilie3d-renderer-smoke >/dev/null 2>&1; then
    docker tag soilie3d-renderer-smoke "$uri"
  else
    docker build --platform linux/amd64 --provenance=false -f "$dockerfile" -t "$uri" .
  fi
  docker push "$uri"
}

build_and_push soilie3d-api serverless/api/Dockerfile "$api_uri"
build_and_push soilie3d-renderer serverless/renderer/Dockerfile "$renderer_uri"

aws cloudformation deploy \
  --region "$region" \
  --stack-name "$stack" \
  --template-file serverless/infra/template.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --role-arn "arn:aws:iam::${account_id}:role/soilie3d-cloudformation-service" \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ApiImageUri=${api_uri}" "RendererImageUri=${renderer_uri}" \
    "ModelVersion=${model_version}" "SourceCommit=${tag}" \
    "AssetManifestSha256=${asset_sha}" "ImplementationChannel=main"

aws cloudformation describe-stacks --region "$region" --stack-name "$stack" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text
