FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS vaporetto

ARG TARGETARCH
ARG SQLITE_VAPORETTO_VERSION=0.4.0
ARG SQLITE_VAPORETTO_AMD64_SHA256=2a373575ea12d3617ce495c428f87ac714ada38c5f87627f9e1434c1b12677d1
ARG SQLITE_VAPORETTO_ARM64_SHA256=bb79343bdb430bb120a4a9d3fe42a954685813be74fb39030ca7c1ca4e8975b0

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) vaporetto_arch="x86_64"; vaporetto_sha256="$SQLITE_VAPORETTO_AMD64_SHA256" ;; \
      arm64) vaporetto_arch="aarch64"; vaporetto_sha256="$SQLITE_VAPORETTO_ARM64_SHA256" ;; \
      *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    archive="/tmp/sqlite-vaporetto.tar.gz"; \
    release_root="sqlite-vaporetto-v${SQLITE_VAPORETTO_VERSION}-linux-${vaporetto_arch}-with-model"; \
    curl --fail --location --retry 3 \
      "https://github.com/hotchpotch/sqlite-vaporetto/releases/download/v${SQLITE_VAPORETTO_VERSION}/${release_root}.tar.gz" \
      --output "$archive"; \
    echo "${vaporetto_sha256}  ${archive}" | sha256sum --check --strict; \
    mkdir -p /opt/sqlite-vaporetto/lib /opt/sqlite-vaporetto/doc /tmp/sqlite-vaporetto; \
    tar -xzf "$archive" -C /tmp/sqlite-vaporetto; \
    cp "/tmp/sqlite-vaporetto/${release_root}/libsqlite_vaporetto.so" /opt/sqlite-vaporetto/lib/; \
    cp "/tmp/sqlite-vaporetto/${release_root}/README.md" \
       "/tmp/sqlite-vaporetto/${release_root}/MODEL_LICENSES.md" \
       /opt/sqlite-vaporetto/doc/

FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

ARG MRAG_SOURCE_COMMIT=84e6ca99bebfe58dcd6c358aacdcc61d7d83eb49
ARG SQLITE_VAPORETTO_VERSION=0.4.0
ARG PROJECT_VERSION=1.0.0
ARG PYTORCH_VERSION=2.12.0

LABEL org.opencontainers.image.title="mrag runtime"
LABEL org.opencontainers.image.revision="${MRAG_SOURCE_COMMIT}"
LABEL org.opencontainers.image.sqlite-vaporetto.version="${SQLITE_VAPORETTO_VERSION}"
LABEL org.opencontainers.image.version="${PROJECT_VERSION}"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/bathtimefish/mrag_dify_docker"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MRAG_VAPORETTO_LIB=/usr/local/lib/libsqlite_vaporetto.so \
    HF_HOME=/home/mrag/.cache/huggingface

RUN apt-get update \
    && apt-get install --yes --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 mrag \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin mrag

WORKDIR /src/mrag
COPY vendor/mrag/ ./
COPY constraints-runtime.txt /tmp/constraints-runtime.txt
COPY --from=vaporetto /opt/sqlite-vaporetto/lib/libsqlite_vaporetto.so /usr/local/lib/libsqlite_vaporetto.so
COPY --from=vaporetto /opt/sqlite-vaporetto/doc/ /usr/share/doc/sqlite-vaporetto/
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      "torch==${PYTORCH_VERSION}" \
    && pip install --no-cache-dir \
      --constraint /tmp/constraints-runtime.txt \
      ".[vaporetto,reranker]"

RUN mkdir -p /usr/share/licenses/mrag-dify-docker
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/licenses/mrag-dify-docker/

WORKDIR /app
COPY mrag/ /app/mrag/
RUN chmod +x /app/mrag/*.sh

ENTRYPOINT ["/usr/bin/tini", "--"]
