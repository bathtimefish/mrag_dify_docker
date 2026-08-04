# Contributing

[日本語](./CONTRIBUTING-ja.md)

Bug reports, documentation improvements, and focused pull requests are welcome.

## Before opening an issue

1. Search existing issues.
2. Run `./scripts/verify.sh` and the relevant doctor command.
3. Remove secrets, document content, personal data, hostnames, and public IPs
   from logs.
4. Use private vulnerability reporting for security issues.

## Development checks

```bash
python3 -m pip install -r requirements-dev.lock
pytest
ruff check batch mrag scripts tests
shellcheck scripts/*.sh batch/*.sh mrag/*.sh
```

Changes affecting Compose should also pass:

```bash
./scripts/bootstrap-vendors.sh
test -f .env || ./scripts/init-env.sh
./scripts/compose.sh config --quiet
```

Keep English documentation in the default `*.md` file and update the matching
Japanese `*-ja.md` file in the same pull request.

## Pull requests

- Keep changes scoped and explain user-visible behavior.
- Add or update tests.
- Update `CHANGELOG.md` and `CHANGELOG-ja.md` for user-visible changes.
- Update `VERSIONS` and notices when a component, image, artifact, or model changes.
- Never commit `.env`, `kbs_docs/`, generated profiles, backups, logs, or model files.

By contributing, you agree that your contribution is licensed under the MIT
License used by this repository.
