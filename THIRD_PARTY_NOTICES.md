# Third-Party Notices

[日本語](./THIRD_PARTY_NOTICES-ja.md)

The original code in this repository is licensed under the [MIT License](./LICENSE).
The services, libraries, container images, and machine-learning models used by
the system remain subject to their respective licenses. This file is an
informational inventory; the upstream license text is authoritative.

## Core components

| Component | Pinned version | License | How it is used |
|---|---|---|---|
| [Dify](https://github.com/langgenius/dify) | 1.16.0 / `5c6372d2f76d240265b92fd27c16bc772ffcb107` | Modified Apache License 2.0 | Source is downloaded at setup time and its official Compose stack is run |
| [mrag](https://github.com/bathtimefish/mrag) | 1.0.0 / `84e6ca99bebfe58dcd6c358aacdcc61d7d83eb49` | MIT | Git submodule; installed in the custom runtime image |
| [Docling Serve](https://github.com/docling-project/docling-serve) | 1.28.0 | MIT | Digest-pinned container image |
| [Ollama](https://github.com/ollama/ollama) | 0.32.3 | MIT | Containerized embedding service |
| [Qdrant](https://github.com/qdrant/qdrant) | 1.18.3 | Apache-2.0 | Vector database used by mrag |
| [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) | 0.4.0 | MIT OR Apache-2.0 | Shared library copied into the custom runtime image |

The backup and restore tools reuse the locally built, versioned mrag runtime
image and do not introduce a separate helper image.

### Dify additional conditions

Dify does not use the unmodified Apache License 2.0. Its license adds conditions
covering multi-tenant operation and removal or modification of frontend logo and
copyright information. Review the license at the pinned Dify source before
deployment. This project's MIT License does not replace or relax Dify's terms.

### sqlite-vaporetto model

The `-with-model` sqlite-vaporetto release contains the
`bccwj-suw+unidic_pos+kana.model.zst` model, which is distributed under the
BSD-3-Clause license. `mrag/Dockerfile` copies the upstream `README.md` and
`MODEL_LICENSES.md` into `/usr/share/doc/sqlite-vaporetto/` in the runtime image.

## Machine-learning models

| Model | Pinned identifier | Declared license | How it is used |
|---|---|---|---|
| [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) | Ollama `bge-m3` manifest recorded in `VERSIONS` | MIT | Embeddings; downloaded on first setup |
| [hotchpotch/japanese-reranker-cross-encoder-small-v1](https://huggingface.co/hotchpotch/japanese-reranker-cross-encoder-small-v1) | Hugging Face revision recorded in `VERSIONS` | MIT | Reranking; downloaded on first setup |

Model licenses and artifacts must be reviewed again whenever a model identifier,
revision, or digest is updated.

## Transitive dependencies and container operating systems

The custom images also contain Python, Debian packages, PyTorch, and the
transitive dependencies of mrag. Their license files remain in their normal
package or image locations. Release builds generate a Software Bill of Materials
(SBOM); use it as the exact inventory for a particular build.

The Docling CPU and NVIDIA images are separate artifacts. The NVIDIA image also
contains NVIDIA CUDA components governed by NVIDIA's terms. Users selecting the
NVIDIA profile are responsible for reviewing those terms for their deployment.

For mrag's detailed Python dependency inventory, see
`vendor/mrag/THIRD_PARTY_NOTICES.md` after initializing the submodule.
