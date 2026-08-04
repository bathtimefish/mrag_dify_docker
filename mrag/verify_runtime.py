#!/usr/bin/env python3
from __future__ import annotations

import os

import httpx
from qdrant_client import QdrantClient

ollama_url = os.environ.get(
    "MRAG_OLLAMA_URL", "http://ollama:11434"
).rstrip("/")
embedding_model = os.environ.get("MRAG_EMBEDDING_MODEL", "bge-m3:latest")

response = httpx.get(f"{ollama_url}/api/tags", timeout=10)
response.raise_for_status()
model_names = {
    item.get("name") for item in response.json().get("models", []) if item.get("name")
}
if embedding_model not in model_names:
    raise RuntimeError(
        f"required Ollama model {embedding_model!r} was not found at {ollama_url}"
    )

embed_response = httpx.post(
    f"{ollama_url}/api/embed",
    json={"model": embedding_model, "input": "mrag runtime health check"},
    timeout=120,
)
embed_response.raise_for_status()
embeddings = embed_response.json().get("embeddings", [])
if len(embeddings) != 1 or not embeddings[0]:
    raise RuntimeError(
        f"embedding probe for {embedding_model!r} returned no vector"
    )
embedding_dimensions = len(embeddings[0])

qdrant_host = os.environ.get("MRAG_QDRANT_HOST", "mrag-qdrant")
qdrant_port = int(os.environ.get("MRAG_QDRANT_PORT", "6333"))
QdrantClient(host=qdrant_host, port=qdrant_port).get_collections()

print(
    f"mrag runtime ready: embedding={embedding_model}, "
    f"dimensions={embedding_dimensions}, ollama={ollama_url}, "
    f"qdrant={qdrant_host}:{qdrant_port}"
)
