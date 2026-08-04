#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote


def values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: prepare-dify-env.py OFFICIAL_TEMPLATE PROJECT_ENV OUTPUT",
            file=sys.stderr,
        )
        return 2
    official, project_env, output = map(Path, sys.argv[1:])
    overrides = values(project_env)

    # Dify's official template contains a CELERY_BROKER_URL with its default
    # Redis password embedded in it. When this project replaces REDIS_PASSWORD,
    # derive the broker URL as well unless the operator explicitly supplied one.
    if "REDIS_PASSWORD" in overrides and "CELERY_BROKER_URL" not in overrides:
        redis_password = quote(overrides["REDIS_PASSWORD"], safe="")
        overrides["CELERY_BROKER_URL"] = (
            f"redis://:{redis_password}@redis:6379/1"
        )

    rendered: list[str] = []
    seen: set[str] = set()

    for line in official.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in overrides:
                line = f"{key}={overrides[key]}"
                seen.add(key)
        rendered.append(line)

    rendered.extend(
        f"{key}={value}"
        for key, value in overrides.items()
        if key not in seen
    )
    output.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    output.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
