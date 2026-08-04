# Security Policy

[日本語](./SECURITY-ja.md)

## Supported versions

Security fixes are provided for the latest released minor version.

| Version | Supported |
|---|---|
| 1.x | Yes |
| < 1.0 | No |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
vulnerability reporting feature for this repository. Include:

- affected version or commit;
- deployment profile and operating system;
- reproduction steps and impact;
- whether credentials or confidential documents may have been exposed;
- a suggested fix, if available.

Remove API keys, passwords, document contents, customer names, hostnames, and
public IP addresses from logs and screenshots. We will acknowledge a complete
report, assess severity, and coordinate disclosure and a fix with the reporter.

## Deployment responsibility

This repository assembles independently maintained services. Operators must
keep the host, Docker, Dify, mrag, Docling, Ollama, Qdrant, and downloaded models
updated; restrict network access; enable HTTPS; and protect backups and secrets.
See [Deployment security](./docs/security.md).
