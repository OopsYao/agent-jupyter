from __future__ import annotations

from pathlib import Path

import pytest

from agent_jupyter.cli import main
from agent_jupyter.skill import SkillInstallError, install_skill, skill_text


def test_install_skill_writes_packaged_document(tmp_path: Path) -> None:
    destination = install_skill(tmp_path / "skills")

    assert destination == tmp_path / "skills" / "agent-jupyter" / "SKILL.md"
    assert destination.read_text(encoding="utf-8") == skill_text()
    assert "agent-jupyter exec" in destination.read_text(encoding="utf-8")


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


def test_install_skill_uses_agents_default_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    destination = install_skill()

    assert destination == tmp_path / ".agents" / "skills" / "agent-jupyter" / "SKILL.md"
