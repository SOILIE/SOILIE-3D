FROM public.ecr.aws/lambda/python:3.12

ARG BLENDER_VERSION=3.6.23
ARG BLENDER_ARCHIVE=blender-${BLENDER_VERSION}-linux-x64.tar.xz

RUN dnf install -y \
      tar xz libX11 libXi libXrender libXfixes libXcursor libXinerama libXrandr \
      mesa-libGL mesa-libEGL libSM libICE libwayland-client libxkbcommon fontconfig \
    && dnf clean all \
    && curl --fail --location --retry 4 \
      "https://download.blender.org/release/Blender3.6/${BLENDER_ARCHIVE}" \
      --output /tmp/blender.tar.xz \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz --strip-components=1 -C /opt/blender \
    && rm /tmp/blender.tar.xz

RUN pip install --no-cache-dir numpy==1.26.4 pandas==2.1.4 Pillow==10.4.0

COPY serverless /var/task/serverless
COPY .codex/runtime/v4 /var/task/v4

ENV BLENDER_PATH=/opt/blender/blender
ENV V4_RUNTIME_DIR=/var/task/v4
ENV PYTHONHASHSEED=0
CMD ["serverless.renderer.handler.lambda_handler"]
