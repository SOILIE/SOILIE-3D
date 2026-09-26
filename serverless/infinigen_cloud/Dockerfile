# The exact, already validated Indoors release and Blender installation are
# supplied as named BuildKit contexts. No model source is rewritten for Lambda.
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates libgl1 libglu1-mesa libxi6 libxrender1 libxfixes3 libxkbcommon0 libsm6 libgomp1 libegl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir awslambdaric==3.1.1 boto3==1.40.40
COPY --from=blender / /opt/blender/
COPY --from=dependencies / /opt/dependencies/
COPY --from=infinigen / /opt/infinigen/
COPY --from=backend /benchmark/ /var/task/serverless/benchmark/
COPY handler.py /var/task/serverless/infinigen_cloud/handler.py
RUN chmod +x /opt/blender/blender && /opt/blender/blender --version
ENV PYTHONPATH=/var/task:/opt/dependencies:/opt/infinigen HOME=/tmp XDG_CACHE_HOME=/tmp/cache PYTHONHASHSEED=0 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /var/task
ENTRYPOINT ["python3", "-m", "awslambdaric"]
CMD ["serverless.infinigen_cloud.handler.lambda_handler"]
