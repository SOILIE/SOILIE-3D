# This image supplies only the OS libraries and supervision interpreter.
# The original Blender/Python/Infinigen/package bytes are read-only mounts.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 ca-certificates libx11-6 libxrender1 libxxf86vm1 libxfixes3 \
      libxi6 libxkbcommon0 libsm6 libice6 libgl1 libegl1 libgomp1 \
      libglib2.0-0t64 libfontconfig1 libfreetype6 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["/usr/bin/python3"]
