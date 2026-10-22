# The required digest-pinned renderer base supplies Blender and the immutable
# assets/data from the same model checkout. Code and provenance are recopied so
# the build check below proves that the complete runtime agrees byte-for-byte.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY modules /var/task/soilie-runtime/modules
COPY serverless /var/task/serverless
COPY package.json /var/task/package.json
COPY provenance.json /var/task/soilie-runtime/model-provenance.json
ENV SOILIE_RUNTIME_DIR=/var/task/soilie-runtime
RUN python -c "from serverless.cloud_benchmark.handler import verify_build_inputs; verify_build_inputs(); import hashlib,pathlib; pathlib.Path('/var/task/benchmark-inputs.sha256').write_text(hashlib.sha256(pathlib.Path('/var/task/soilie-runtime/model-provenance.json').read_bytes()).hexdigest())"
# Exercise Blender's actual interpreter and both distinct package roots before
# any paid Lambda invocation; checkout-only imports do not prove image parity.
RUN PYTHONPATH=/var/task:/var/task/soilie-runtime ${BLENDER_PATH} --background --factory-startup --threads 1 --python-exit-code 2 --python-expr "from serverless.benchmark.mesh_contact import measure_contacts; from modules.support_settlement import surface_drop; print('contact imports verified')"
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
CMD ["serverless.cloud_benchmark.handler.lambda_handler"]
