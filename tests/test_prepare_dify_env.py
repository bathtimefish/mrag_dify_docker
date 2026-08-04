from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare-dify-env.py"


def run_prepare(
    tmp_path: Path,
    project_lines: str,
) -> dict[str, str]:
    official = tmp_path / "official.env"
    project = tmp_path / "project.env"
    output = tmp_path / "output.env"
    official.write_text(
        "REDIS_PASSWORD=difyai123456\n"
        "CELERY_BROKER_URL=redis://:difyai123456@redis:6379/1\n",
        encoding="utf-8",
    )
    project.write_text(project_lines, encoding="utf-8")

    subprocess.run(
        [sys.executable, str(SCRIPT), str(official), str(project), str(output)],
        check=True,
    )
    return dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def test_celery_url_is_derived_from_redis_password(tmp_path: Path) -> None:
    result = run_prepare(tmp_path, "REDIS_PASSWORD=new_secret-123\n")

    assert result["REDIS_PASSWORD"] == "new_secret-123"
    assert (
        result["CELERY_BROKER_URL"]
        == "redis://:new_secret-123@redis:6379/1"
    )


def test_explicit_celery_url_is_preserved(tmp_path: Path) -> None:
    result = run_prepare(
        tmp_path,
        "REDIS_PASSWORD=new_secret\n"
        "CELERY_BROKER_URL=redis://:explicit@custom-redis:6380/4\n",
    )

    assert (
        result["CELERY_BROKER_URL"]
        == "redis://:explicit@custom-redis:6380/4"
    )
