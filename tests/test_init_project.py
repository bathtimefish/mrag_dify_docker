from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "mrag" / "init-project.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def prepare_fake_commands(path: Path) -> Path:
    bin_dir = path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "mrag",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "init" ]]
target="$2"
mkdir -p "$target/profiles"
printf 'name: default\\n' > "$target/profiles/default.yaml"
printf 'default prompt\\n' > "$target/profiles/context_prompt.txt"
printf '{}\\n' > "$target/mrag.yaml"
""",
    )
    write_executable(bin_dir / "python", "#!/usr/bin/env bash\nexit 0\n")
    write_executable(bin_dir / "chown", "#!/usr/bin/env bash\nexit 0\n")
    return bin_dir


def run_init(project_dir: Path, fake_bin: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "MRAG_PROJECT_DIR": str(project_dir),
            "HF_HOME": str(project_dir.parent / "cache" / "huggingface"),
        }
    )
    subprocess.run(
        ["bash", str(SCRIPT)],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_restores_missing_profiles_without_reinitializing_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "mrag.yaml").write_text("existing: config\n", encoding="utf-8")
    fake_bin = prepare_fake_commands(tmp_path)

    run_init(project_dir, fake_bin)

    assert (project_dir / "mrag.yaml").read_text(encoding="utf-8") == "existing: config\n"
    assert (profiles_dir / "default.yaml").read_text(encoding="utf-8") == "name: default\n"
    assert (
        profiles_dir / "context_prompt.txt"
    ).read_text(encoding="utf-8") == "default prompt\n"


def test_preserves_existing_profile_when_restoring_other_file(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "mrag.yaml").write_text("existing: config\n", encoding="utf-8")
    (profiles_dir / "default.yaml").write_text("custom: profile\n", encoding="utf-8")
    fake_bin = prepare_fake_commands(tmp_path)

    run_init(project_dir, fake_bin)

    assert (profiles_dir / "default.yaml").read_text(encoding="utf-8") == "custom: profile\n"
    assert (profiles_dir / "context_prompt.txt").exists()


def test_preserves_all_profile_files_when_project_volume_is_initialized(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    profiles_dir = project_dir / "profiles"
    custom_dir = profiles_dir / "custom"
    custom_dir.mkdir(parents=True)
    (profiles_dir / "default.yaml").write_text("custom: profile\n", encoding="utf-8")
    (profiles_dir / "context_prompt.txt").write_text("custom prompt\n", encoding="utf-8")
    (profiles_dir / "alternative.yaml").write_text(
        "name: alternative\n",
        encoding="utf-8",
    )
    (profiles_dir / ".profile-state").write_text("keep\n", encoding="utf-8")
    (custom_dir / "prompt.txt").write_text("nested prompt\n", encoding="utf-8")
    fake_bin = prepare_fake_commands(tmp_path)

    run_init(project_dir, fake_bin)

    assert (project_dir / "mrag.yaml").exists()
    assert (profiles_dir / "default.yaml").read_text(encoding="utf-8") == "custom: profile\n"
    assert (
        profiles_dir / "context_prompt.txt"
    ).read_text(encoding="utf-8") == "custom prompt\n"
    assert (
        profiles_dir / "alternative.yaml"
    ).read_text(encoding="utf-8") == "name: alternative\n"
    assert (profiles_dir / ".profile-state").read_text(encoding="utf-8") == "keep\n"
    assert (
        custom_dir / "prompt.txt"
    ).read_text(encoding="utf-8") == "nested prompt\n"
