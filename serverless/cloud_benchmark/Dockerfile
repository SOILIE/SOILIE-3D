# Reuse only the immutable Blender/dependency/asset layers. Model and observer
# source come directly from this checkout, never from the deployed older model.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY modules /var/task/v4/modules
COPY serverless /var/task/serverless
COPY package.json /var/task/package.json
COPY provenance.json /var/task/v4/v4-provenance.json
RUN python -c "from serverless.cloud_benchmark.handler import verify_build_inputs; verify_build_inputs(); import hashlib,pathlib; pathlib.Path('/var/task/benchmark-inputs.sha256').write_text(hashlib.sha256(pathlib.Path('/var/task/v4/v4-provenance.json').read_bytes()).hexdigest())"
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
CMD ["serverless.cloud_benchmark.handler.lambda_handler"]
