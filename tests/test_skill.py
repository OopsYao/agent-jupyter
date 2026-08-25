from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import agent_jupyter.cli as cli
from agent_jupyter.cli import main
from agent_jupyter.skill import (
    SkillInstallError,
    install_skill,
    package_skill,
    skill_text,
)


def test_list_outputs_discovery_completion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "discover_kernels",
        lambda runtime_dirs, **kwargs: {"kernels": [], "complete": False},
    )

    assert main(["list", "--timeout", "0.1"]) == 0

    assert json.loads(capsys.readouterr().out) == {"kernels": [], "complete": False}


def test_list_outputs_compact_kernel_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = {
        "kernels": [
            {
                "connection": str(tmp_path / "kernel.json"),
                "status": "identified",
                "language": {"name": "python", "version": "3.12"},
                "implementation": {"name": "ipykernel", "version": "6"},
            }
        ],
        "complete": True,
    }
    monkeypatch.setattr(cli, "discover_kernels", lambda runtime_dirs, **kwargs: result)

    assert main(["list"]) == 0

    assert json.loads(capsys.readouterr().out) == result


def test_install_skill_writes_packaged_document(tmp_path: Path) -> None:
    destination = install_skill(tmp_path / "skills")

    assert destination == tmp_path / "skills" / "agent-jupyter" / "SKILL.md"
    assert destination.read_text(encoding="utf-8") == skill_text()


def test_install_skill_refuses_to_overwrite_by_default(tmp_path: Path) -> None:
    destination = install_skill(tmp_path)
    original = destination.read_text(encoding="utf-8")
    destination.write_text("keep this file", encoding="utf-8")

    with pytest.raises(SkillInstallError, match="refusing to overwrite"):
        install_skill(tmp_path)

    assert destination.read_text(encoding="utf-8") == "keep this file"
    assert original != destination.read_text(encoding="utf-8")


def test_install_skill_force_replaces_existing_document(tmp_path: Path) -> None:
    destination = install_skill(tmp_path)
    destination.write_text("old document", encoding="utf-8")

    assert install_skill(tmp_path, force=True) == destination
    assert destination.read_text(encoding="utf-8") == skill_text()


def test_install_skill_command_accepts_explicit_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-skill", "--skills-root", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == str(tmp_path / "agent-jupyter" / "SKILL.md")
    assert captured.err == ""


def test_package_skill_writes_distributable_archive(tmp_path: Path) -> None:
    destination = package_skill(tmp_path / "agent-jupyter-skill.zip")

    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["agent-jupyter/SKILL.md"]
        assert archive.read("agent-jupyter/SKILL.md").decode() == skill_text()


def test_package_skill_refuses_to_overwrite_by_default(tmp_path: Path) -> None:
    destination = tmp_path / "skill.zip"
    destination.write_bytes(b"keep this archive")

    with pytest.raises(SkillInstallError, match="refusing to overwrite"):
        package_skill(destination)

    assert destination.read_bytes() == b"keep this archive"


def test_package_skill_command_can_force_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "skill.zip"
    destination.write_bytes(b"old archive")

    assert main(["package-skill", "--output", str(destination), "--force"]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == str(destination)
    assert captured.err == ""
    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["agent-jupyter/SKILL.md"]


def test_install_skill_uses_agents_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    destination = install_skill()

    assert destination == tmp_path / ".agents" / "skills" / "agent-jupyter" / "SKILL.md"
