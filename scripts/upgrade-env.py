#!/usr/bin/env python3
"""Add missing template settings to an existing .env without changing values."""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path

SECRET_KEYS = {
    "DB_PASSWORD",
    "REDIS_PASSWORD",
    "QDRANT_API_KEY",
    "SECRET_KEY",
    "PLUGIN_DAEMON_KEY",
    "PLUGIN_DIFY_INNER_API_KEY",
    "DIFY_AGENT_SERVER_SECRET_KEY",
    "MRAG_API_KEY",
    "DOCLING_SERVE_API_KEY",
}


def assignment(line: str) -> tuple[str, str] | None:
    key, separator, value = line.partition("=")
    if not separator or not key or key.startswith("#") or key.strip() != key:
        return None
    return key, value


def generated_value(key: str, value: str) -> str:
    if key not in SECRET_KEYS or value != "CHANGE_ME":
        return value
    byte_count = 32 if key == "DIFY_AGENT_SERVER_SECRET_KEY" else 48
    return secrets.token_urlsafe(byte_count)


def add_missing(template: Path, destination: Path) -> tuple[str, ...]:
    if not destination.is_file():
        raise FileNotFoundError(f"{destination} does not exist; run init-env first")
    current = destination.read_text(encoding="utf-8")
    existing = {
        parsed[0]
        for line in current.splitlines()
        if (parsed := assignment(line)) is not None
    }
    additions: list[str] = []
    added_keys: list[str] = []
    for line in template.read_text(encoding="utf-8").splitlines():
        parsed = assignment(line)
        if parsed is None:
            continue
        key, value = parsed
        if key in existing:
            continue
        additions.append(f"{key}={generated_value(key, value)}")
        added_keys.append(key)

    if not additions:
        return ()

    mode = destination.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(current.rstrip("\n"))
            stream.write("\n\n# Added from .env.example by upgrade-env.py\n")
            stream.write("\n".join(additions))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return tuple(added_keys)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: upgrade-env.py TEMPLATE DESTINATION", file=sys.stderr)
        return 2
    try:
        added = add_missing(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if added:
        print(f"Added {len(added)} missing settings; existing values were preserved.")
    else:
        print("No missing settings; existing file was unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
