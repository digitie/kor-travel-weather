"""Pin the Dagster the images install to one exact, locked version set.

Dagster's compatibility rule is one-directional: a host process (webserver,
daemon) may serve code servers of its own version *or older*, never newer.
While this project ran its own webserver and daemon from the same image as its
code server, that rule held by construction.  Once a shared host serves several
projects' code servers, it holds only if every code server installs exactly the
version the host was built with -- a floor such as ``dagster>=1.9,<2`` lets the
next rebuild or ``uv lock --upgrade`` resolve a newer release and leave the
supported window without anyone touching this repository's code.

These tests fail when that exactness is lost at any of the three places it can
be lost: the declared requirement, the lock the image installs from, and the
image's own install command and base.
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCKFILE = REPO_ROOT / "uv.lock"
PYTHON_DOCKERFILE = REPO_ROOT / "deploy" / "Dockerfile.python"

#: The packages that share dagster's own release number.  dagster-graphql,
#: -pipes and -shared are not declared here; they arrive through dagster's and
#: dagster-webserver's own ``==`` requirements, which is exactly why the lock
#: has to be checked and not just the declaration.
CORE_FAMILY = ("dagster", "dagster-webserver", "dagster-graphql", "dagster-pipes", "dagster-shared")
#: Libraries released alongside core under their own ``0.<minor+16>.<patch>``
#: numbering (dagster 1.13.24 ships with dagster-postgres 0.29.24).
LIBRARY_FAMILY = ("dagster-postgres",)
PYTHON_MINOR = "3.12"


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dagster_extra() -> list[str]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    return project["optional-dependencies"]["dagster"]


def _lock_versions() -> dict[str, str]:
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    for package in lock["package"]:
        name = _normalize(package["name"])
        assert name not in versions, f"uv.lock resolves {name} twice; the set is not one version"
        if "version" in package:
            versions[name] = package["version"]
    return versions


def _library_version_for(core_version: str) -> str:
    major, minor, patch = (int(part) for part in core_version.split("."))
    assert major == 1, f"library numbering rule only verified for dagster 1.x, got {core_version}"
    return f"0.{minor + 16}.{patch}"


def test_every_declared_dagster_requirement_is_exact() -> None:
    requirements = _dagster_extra()
    dagster_requirements = [
        requirement for requirement in requirements if _normalize(requirement).startswith("dagster")
    ]
    assert dagster_requirements, "the [dagster] extra declares no dagster package"
    for requirement in dagster_requirements:
        match = re.fullmatch(r"\s*([A-Za-z0-9_.\-]+)\s*==\s*([0-9][0-9A-Za-z.]*)\s*", requirement)
        assert match, (
            f"{requirement!r} is not an exact '==' pin; a range lets a rebuild resolve a dagster "
            "newer than the host that serves this code server"
        )


def test_the_declared_pins_are_one_release() -> None:
    pins = {}
    for requirement in _dagster_extra():
        name, _, version = requirement.partition("==")
        pins[_normalize(name)] = version.strip()
    core = pins["dagster"]
    for name in CORE_FAMILY:
        if name in pins:
            assert pins[name] == core, f"{name}=={pins[name]} but dagster=={core}"
    for name in LIBRARY_FAMILY:
        if name in pins:
            assert pins[name] == _library_version_for(core), (
                f"{name}=={pins[name]} does not ship with dagster=={core}"
            )


def test_the_lock_resolves_exactly_the_declared_release() -> None:
    declared = {}
    for requirement in _dagster_extra():
        name, _, version = requirement.partition("==")
        declared[_normalize(name)] = version.strip()
    locked = _lock_versions()
    core = declared["dagster"]
    # Every family member the lock carries -- declared or transitive -- has to
    # be the same release, or the image installs a mixed set.
    for name in CORE_FAMILY:
        assert locked.get(name) == core, f"uv.lock has {name} {locked.get(name)}, expected {core}"
    for name in LIBRARY_FAMILY:
        expected = _library_version_for(core)
        assert locked.get(name) == expected, (
            f"uv.lock has {name} {locked.get(name)}, expected {expected}"
        )


def _dockerfile_instructions() -> list[str]:
    """Dockerfile instructions with ``\\`` continuations joined and comments dropped."""
    text = PYTHON_DOCKERFILE.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return [instruction.strip() for instruction in "\n".join(lines).replace("\\\n", " ").split("\n")]


def test_the_image_installs_only_from_the_lock() -> None:
    instructions = _dockerfile_instructions()
    installs = [
        instruction
        for instruction in instructions
        if re.search(r"\b(uv\s+(sync|pip)|pip3?\s+install)\b", instruction)
    ]
    assert installs, "Dockerfile.python installs nothing; the check below would pass vacuously"
    for instruction in installs:
        assert "pip install" not in instruction and "uv pip" not in instruction, (
            f"{instruction!r} installs outside the lock"
        )
        for sync in re.findall(r"uv\s+sync[^;&|]*", instruction):
            assert "--locked" in sync.split(), f"{sync!r} may re-resolve instead of installing uv.lock"


def test_the_image_runs_a_digest_pinned_python_312() -> None:
    bases = [
        instruction.split()[1]
        for instruction in _dockerfile_instructions()
        if instruction.upper().startswith("FROM ") and "python:" in instruction
    ]
    assert len(bases) == 1, f"expected one python base image, found {bases}"
    image = bases[0]
    match = re.fullmatch(r"python:(\d+\.\d+)[0-9.]*-slim@sha256:[0-9a-f]{64}", image)
    assert match, f"{image!r} is not a digest-pinned python slim image"
    assert match.group(1) == PYTHON_MINOR, f"{image!r} is not Python {PYTHON_MINOR}"


def test_the_lock_admits_the_image_python() -> None:
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    floor = re.fullmatch(r">=\s*(\d+)\.(\d+)", lock["requires-python"].strip())
    assert floor, f"unexpected requires-python {lock['requires-python']!r}"
    image_minor = tuple(int(part) for part in PYTHON_MINOR.split("."))
    assert (int(floor.group(1)), int(floor.group(2))) <= image_minor


def test_the_running_environment_is_the_locked_one() -> None:
    """The suite itself must run on the set it claims to pin.

    Skipped only where the ``dagster`` extra is not installed at all; an
    environment that has it, but at another version, is the drift this file
    exists to catch.
    """
    try:
        installed = importlib.metadata.version("dagster")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("the dagster extra is not installed")
    locked = _lock_versions()
    for name in (*CORE_FAMILY, *LIBRARY_FAMILY):
        assert importlib.metadata.version(name) == locked[name], (
            f"installed {name} {importlib.metadata.version(name)} != uv.lock {locked[name]}"
        )
    assert installed == locked["dagster"]
