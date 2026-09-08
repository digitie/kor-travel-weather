"""Guard the wiring that makes ``/version`` describe the code that is running.

The deployed API reported ``git_commit: 6003da9`` while running four commits
later.  The value came from ``KOR_TRAVEL_WEATHER_GIT_COMMIT`` in the deployment
env file, injected at run time: nothing tied it to the image, so it was set once
by hand and then quietly went stale.  A deploy check that reads ``/version`` to
confirm what shipped therefore confirmed nothing.

The revision is now a build argument baked into the image.  These tests fail if
anyone puts a run-time override back, because a run-time value wins over the
baked one and restores the drift.

Compose accepts an environment override in four shapes -- a mapping, a list, an
``env_file``, and an override compose file -- so checking only the first is
checking almost nothing.  ``docker compose config`` collapses all four, and the
parsing tests below cover the same ground for environments without Docker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = REPO_ROOT / "compose.yaml"
N150_COMPOSE = REPO_ROOT / "deploy" / "compose.n150.yaml"
PYTHON_DOCKERFILE = "deploy/Dockerfile.python"
COMMIT_ENV = "KOR_TRAVEL_WEATHER_GIT_COMMIT"
BUILD_ARG = "GIT_COMMIT"


class _TagTolerantLoader(yaml.SafeLoader):
    """Read compose files that carry merge tags such as ``!override``."""


def _ignore_unknown_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_TagTolerantLoader.add_multi_constructor("!", _ignore_unknown_tag)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader)


def _environment_names(service: dict) -> set[str]:
    """Compose accepts a mapping or a list of ``NAME=value`` strings."""
    environment = service.get("environment")
    if isinstance(environment, dict):
        return set(environment)
    if isinstance(environment, list):
        return {str(entry).split("=", 1)[0] for entry in environment}
    return set()


@pytest.fixture(scope="module")
def compose_files() -> dict[str, dict]:
    return {"compose.yaml": _load(BASE_COMPOSE), "deploy/compose.n150.yaml": _load(N150_COMPOSE)}


@pytest.fixture(scope="module")
def dockerfile_lines() -> list[str]:
    text = (REPO_ROOT / PYTHON_DOCKERFILE).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_no_compose_file_injects_the_revision_at_run_time(
    compose_files: dict[str, dict],
) -> None:
    """Cover every override shape, not just a mapping in the base file.

    A list entry, or an injection in the production-only override, restores the
    original bug exactly -- and the override file is where someone chasing a
    surprising ``/version`` would most naturally put one.
    """
    offenders: list[str] = []
    for filename, document in compose_files.items():
        for name, service in (document.get("services") or {}).items():
            if COMMIT_ENV in _environment_names(service):
                offenders.append(f"{filename}:{name}")
    assert not offenders, (
        f"{offenders} inject {COMMIT_ENV} at run time; a run-time value overrides "
        "the one baked into the image and can drift from the deployed code"
    )


def test_no_service_pulls_an_env_file_into_the_container(
    compose_files: dict[str, dict],
) -> None:
    """``env_file`` would import the deployment env wholesale, stale entry included."""
    offenders = [
        f"{filename}:{name}"
        for filename, document in compose_files.items()
        for name, service in (document.get("services") or {}).items()
        if service.get("env_file")
    ]
    assert not offenders, (
        f"{offenders} declare env_file; that imports every name in the deployment "
        f"env file, so a leftover {COMMIT_ENV} there would override the baked value"
    )


def _python_image_services(document: dict) -> dict[str, dict]:
    return {
        name: service
        for name, service in (document.get("services") or {}).items()
        if isinstance(service.get("build"), dict)
        and service["build"].get("dockerfile") == PYTHON_DOCKERFILE
    }


def test_every_python_image_receives_the_revision_as_a_build_arg(
    compose_files: dict[str, dict],
) -> None:
    services = _python_image_services(compose_files["compose.yaml"])
    assert services, "expected at least one service built from the python image"
    for name, service in services.items():
        args = service["build"].get("args") or {}
        assert BUILD_ARG in args, (
            f"service {name} builds {PYTHON_DOCKERFILE} without a {BUILD_ARG} build "
            "arg, so its image cannot report the revision it contains"
        )


def test_the_build_arg_comes_from_the_caller_not_a_literal(
    compose_files: dict[str, dict],
) -> None:
    """A literal here is the same hand-pinned value this change removed."""
    for name, service in _python_image_services(compose_files["compose.yaml"]).items():
        value = str(service["build"]["args"][BUILD_ARG])
        assert value.startswith("${"), (
            f"service {name} pins {BUILD_ARG}={value!r}; a literal would be baked "
            "into every image forever, which is the failure this change removed"
        )


def test_the_python_image_bakes_the_revision_after_the_final_stage(
    dockerfile_lines: list[str],
) -> None:
    """Placement is the whole mechanism, so assert it rather than a substring.

    A build argument declared before the final ``FROM`` is out of scope there:
    the image ends up with no variable at all, or with an empty one, and
    ``/version`` answers null. Comments are stripped so a commented-out block
    cannot satisfy this either.
    """
    final_from = max(
        index for index, line in enumerate(dockerfile_lines) if line.startswith("FROM ")
    )
    arg_lines = [
        i for i, line in enumerate(dockerfile_lines) if line.startswith(f"ARG {BUILD_ARG}")
    ]
    env_lines = [
        i for i, line in enumerate(dockerfile_lines) if line.startswith(f"ENV {COMMIT_ENV}=")
    ]
    assert arg_lines, f"no active `ARG {BUILD_ARG}` line"
    assert env_lines, f"no active `ENV {COMMIT_ENV}=` line"
    assert min(arg_lines) > final_from, (
        f"ARG {BUILD_ARG} is declared before the final FROM, so it is out of scope "
        "in the stage that ships and the image would carry no revision"
    )
    assert min(env_lines) > min(arg_lines), "ENV must follow the ARG it reads"
    assert dockerfile_lines[min(env_lines)].strip() == f"ENV {COMMIT_ENV}=${{{BUILD_ARG}}}"


def test_the_unset_default_is_honest(dockerfile_lines: list[str]) -> None:
    """An unknown revision must read as unknown, not as something plausible.

    The previous default was the literal ``container``, which looks like an
    answer.  ``unknown`` cannot be mistaken for one.
    """
    arg_line = next(line for line in dockerfile_lines if line.startswith(f"ARG {BUILD_ARG}"))
    assert arg_line.strip() == f"ARG {BUILD_ARG}=unknown"


def test_the_example_env_does_not_pin_a_revision() -> None:
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert COMMIT_ENV not in example, (
        f"{COMMIT_ENV} in .env.example invites operators to pin a value by hand, "
        "which is how the deployed API came to report a stale commit"
    )
    assert f"\n{BUILD_ARG}=" not in f"\n{example}", (
        f"{BUILD_ARG} in .env.example would be picked up by compose interpolation "
        "and baked into every image, reintroducing the hand-pinned value"
    )


def test_every_documented_build_command_passes_the_revision() -> None:
    """The same build command is copied into several documents.

    Fixing one of them leaves the others baking ``unknown`` -- and one of those
    documents then tells the operator to smoke-check ``/version``.
    """
    documents = [
        REPO_ROOT / "deploy" / "n150.md",
        REPO_ROOT / "deploy" / "README.md",
        REPO_ROOT / "docs" / "runbooks" / "docker-app.md",
    ]
    offenders: list[str] = []
    for document in documents:
        lines = document.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "up -d --build" not in line:
                continue
            # A shell invocation may be wrapped, so walk back over continuations
            # to find where the command actually starts.
            start = index
            while start > 0 and lines[start - 1].rstrip().endswith("\\"):
                start -= 1
            if BUILD_ARG not in " ".join(lines[start : index + 1]):
                offenders.append(f"{document.relative_to(REPO_ROOT)}:{index + 1}")
    assert not offenders, (
        f"{offenders} build without {BUILD_ARG}; those images would report an "
        "unknown revision while the surrounding runbook says to verify /version"
    )


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not available")
def test_rendered_compose_never_carries_the_revision_into_a_container() -> None:
    """Belt and braces: let compose itself collapse every override shape.

    A stale entry is deliberately supplied so this fails if any path -- mapping,
    list, env_file, or override file -- would carry it into a container.
    """
    env_file = REPO_ROOT / "tests" / "_deployment_contract.env"
    env_file.write_text(
        "\n".join(
            [
                f"{COMMIT_ENV}=stale0000",
                "POSTGRES_PASSWORD=x",
                "KOR_TRAVEL_WEATHER_ADMIN_TOKEN=x",
                "KOR_TRAVEL_WEATHER_METRICS_TOKEN=xxxxxxxxxxxxxxxx",
                "WEATHER_UI_PASSWORD=x",
                "WEATHER_UI_SESSION_SECRET=" + "s" * 40,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(  # noqa: S603
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(BASE_COMPOSE),
                "-f",
                str(N150_COMPOSE),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=120,
            check=False,
        )
    finally:
        env_file.unlink(missing_ok=True)
    if completed.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {completed.stderr.strip()[:200]}")

    rendered = json.loads(completed.stdout)
    offenders = [
        name
        for name, service in rendered["services"].items()
        if COMMIT_ENV in (service.get("environment") or {})
    ]
    assert not offenders, (
        f"{offenders} receive {COMMIT_ENV} in the rendered configuration even though "
        "it is only set in the env file; a run-time value overrides the baked one"
    )


def test_the_application_reads_the_name_the_image_bakes(monkeypatch) -> None:
    """Bind the Dockerfile's variable name to the one the settings actually read.

    Without this the image can keep baking a value under a name nothing loads:
    every check above would still pass while ``/version`` answers null.
    """
    from kortravelweather.settings import WeatherSettings

    monkeypatch.setenv(COMMIT_ENV, "deadbee")
    assert WeatherSettings(_env_file=None).git_commit == "deadbee"

    monkeypatch.delenv(COMMIT_ENV, raising=False)
    assert WeatherSettings(_env_file=None).git_commit is None
