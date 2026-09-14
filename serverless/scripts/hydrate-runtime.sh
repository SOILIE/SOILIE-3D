#!/usr/bin/env bash
set -euo pipefail

bucket="${SOILIE_RUNTIME_BUCKET:-soilie3d-data}"
region="${AWS_REGION:-ca-central-1}"

aws s3 sync "s3://${bucket}/files/assets/" assets/ \
  --region "$region" --exclude "*" --include "*.obj" --include "asset_rotations.csv" --only-show-errors
aws s3 sync "s3://${bucket}/files/data/" data/ \
  --region "$region" --exclude "*" \
  --include "object_colors.csv" --include "object_sizes_manual.csv" --include "triplets.csv" \
  --include "working-combos-refined.csv" --include "working-combos-bedroom.csv" \
  --include "working-combos-livingroom.csv" --include "working-combos-kitchen.csv" \
  --include "working-combos-bathroom.csv" --only-show-errors
python -m serverless.compiler.runtime_assets verify --repository . --manifest serverless/runtime-assets.json
