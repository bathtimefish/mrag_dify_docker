#!/usr/bin/env python3
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: generate-env.py TEMPLATE DESTINATION", file=sys.stderr)
        return 2
    template = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    if destination.exists():
        print(f"{destination} already exists; leaving it unchanged.", file=sys.stderr)
        return 1

    secret_keys = {
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
    output: list[str] = []
    for line in template.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in secret_keys and value == "CHANGE_ME":
            byte_count = 32 if key == "DIFY_AGENT_SERVER_SECRET_KEY" else 48
            line = f"{key}={secrets.token_urlsafe(byte_count)}"
        output.append(line)

    destination.write_text("\n".join(output) + "\n", encoding="utf-8")
    if os.name == "nt":
        print(
            f"Generated {destination}. Access is controlled by its inherited "
            "Windows ACL."
        )
    else:
        destination.chmod(0o600)
        print(f"Generated {destination} with mode 0600.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
