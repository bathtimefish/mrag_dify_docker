ARG MRAG_RUNTIME_IMAGE=local/mrag-runtime:1.0.0
FROM ${MRAG_RUNTIME_IMAGE}

ARG MRAG_SOURCE_COMMIT=84e6ca99bebfe58dcd6c358aacdcc61d7d83eb49
ARG PROJECT_VERSION=1.0.1

LABEL org.opencontainers.image.title="mrag synchronization batch"
LABEL org.opencontainers.image.revision="${MRAG_SOURCE_COMMIT}"
LABEL org.opencontainers.image.version="${PROJECT_VERSION}"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/bathtimefish/mrag_dify_docker"

USER root

RUN apt-get update \
    && apt-get install --yes --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

COPY batch/ /app/batch/
RUN chmod +x /app/batch/*.sh
