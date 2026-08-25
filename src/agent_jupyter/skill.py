"""Install the built-in agent-jupyter Agent Skill document."""

from __future__ import annotations

import os
import tempfile
import zipfile
from contextlib import suppress
from importlib.resources import files
from pathlib import Path

DEFAULT_SKILLS_ROOT = Path("~/.agents/skills")
SKILL_NAME = "agent-jupyter"
_RESOURCE_NAME = "resources/agent_jupyter_skill.md"


class SkillInstallError(Exception):
    """An expected failure while installing the Agent Skill document."""


def skill_text() -> str:
    """Return the packaged Agent Skill document."""
    return files("agent_jupyter").joinpath(_RESOURCE_NAME).read_text(encoding="utf-8")


def package_skill(output: str | Path, *, force: bool = False) -> Path:
    """Write the built-in skill as a distributable ZIP archive."""
    destination = Path(output).expanduser()
    if destination.exists() and not force:
        raise SkillInstallError(
            f"skill package already exists at {destination}; refusing to overwrite "
            "(use --force to replace it)"
        )

    temporary_name: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as stream:
            temporary_name = stream.name
        with zipfile.ZipFile(temporary_name, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{SKILL_NAME}/SKILL.md", skill_text())

        if force:
            os.replace(temporary_name, destination)
        else:
            os.link(temporary_name, destination)
            os.unlink(temporary_name)
        temporary_name = None
    except FileExistsError as exc:
        raise SkillInstallError(
            f"skill package already exists at {destination}; refusing to overwrite "
            "(use --force to replace it)"
        ) from exc
    except OSError as exc:
        raise SkillInstallError(
            f"could not package skill at {destination}: {exc}"
        ) from exc
    finally:
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)

    return destination


def install_skill(
    skills_root: str | Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Install the built-in skill below *skills_root* and return its path.

    The default is intentionally a cross-harness user directory.  Nothing is
    installed as a side effect of importing the package or installing its
    wheel; callers must invoke this function explicitly.
    """
    root = Path(skills_root if skills_root is not None else DEFAULT_SKILLS_ROOT)
    root = root.expanduser()
    destination = root / SKILL_NAME / "SKILL.md"

    if destination.exists() and not force:
        raise SkillInstallError(
            f"skill already exists at {destination}; refusing to overwrite "
            "(use --force to replace it)"
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if force:
            _replace_atomically(destination)
        else:
            # Exclusive creation also preserves the no-overwrite guarantee if
            # another process creates the file after the existence check.
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(skill_text())
    except FileExistsError as exc:
        raise SkillInstallError(
            f"skill already exists at {destination}; refusing to overwrite "
            "(use --force to replace it)"
        ) from exc
    except OSError as exc:
        raise SkillInstallError(f"could not install skill at {destination}: {exc}") from exc

    return destination


def _replace_atomically(destination: Path) -> None:
    """Write a replacement beside *destination*, then replace it."""
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".SKILL.md.",
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(skill_text())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)
