# Deployment Security

[日本語](./security-ja.md) · [Vulnerability reporting](../SECURITY.md)

## Before exposure

- Complete the Dify administrator setup from a trusted network first.
- Put Dify behind HTTPS and a trusted reverse proxy or load balancer.
- Restrict ports 80 and 443 to the intended clients.
- Do not publish mrag, Ollama, Docling, PostgreSQL, Redis, or Qdrant ports.
- Keep the host firewall enabled and patch the host and Docker runtime.

The default mrag host port binds to `127.0.0.1`. Do not change it to
`0.0.0.0` unless an authenticated, encrypted gateway protects it.

## Secrets

`init-env.sh` generates random values and protects `.env` with mode `0600` on
Unix. Store production secrets in an appropriate secret manager where possible.
Rotate credentials after suspected exposure and after transferring ownership.

Never include `.env`, access tokens, API keys, document content, model-provider
credentials, or full diagnostic logs in issues.

## Container and network controls

- Run only required Compose profiles.
- Keep internal services on private Docker networks.
- Preserve non-root users configured for mrag and Docling workloads.
- Pin images and models using `VERSIONS` and review update pull requests.
- Scan release images and SBOMs for vulnerabilities.
- Limit outbound network access to documented image, package, model, and LLM
  provider endpoints.

## Backups

Backups can contain the entire knowledge corpus, Dify database, API keys, and
administrator information. Encrypt them, restrict read access, set retention,
and test restoration. Never upload an unredacted backup to an issue.
