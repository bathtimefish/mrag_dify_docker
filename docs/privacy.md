# Privacy and Data Flow

[日本語](./privacy-ja.md)

This project does not provide a hosted service. The operator controls the host,
documents, credentials, retention, and selected model providers.

## Local processing

Source documents are mounted read-only from `kbs_docs/` into the synchronization
container. Docling converts them inside the Compose network. Converted Markdown,
manifests, indexes, and backups are stored in local Docker volumes. Ollama creates
embeddings locally.

## External transfers

Initial setup contacts container registries, GitHub, Python package indexes,
Ollama's model registry, and Hugging Face to download pinned artifacts and
models. Component updates contact the same upstream services.

When Dify uses an external answer-generation provider, the provider may receive:

- the user's question;
- retrieved document excerpts and metadata;
- system and workflow prompts;
- conversation history configured by the workflow.

Review the selected provider's retention and training policy before indexing
confidential information. A locally hosted Dify-compatible model provider can be
used when data must remain on the operator's infrastructure.

## Logs and support data

Synchronization logs contain relative document paths, hashes, sizes, status,
and error details. They should not contain document bodies or API keys, but file
names alone may be sensitive. Redact logs and screenshots before sharing them.

## Operator checklist

- Confirm that users are authorized to index each document.
- Define retention and deletion requirements for source files, indexes, logs,
  conversations, and backups.
- Restrict host, Docker, Dify, and backup access.
- Document external providers and cross-border transfers where applicable.
- Test that deleting a document removes it from source, index, backups according
  to the operator's policy.
