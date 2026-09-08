"""Guard the wiring that makes ``/version`` describe the code that is running.

The deployed API reported ``git_commit: 6003da9`` while running four commits
later.  The value came from ``KOR_TRAVEL_WEATHER_GIT_COMMIT`` in the deployment
env file, injected at run time: nothing tied it to the image, so it was set once
by hand and then quietly went stale.  A deploy check that reads ``/version`` to
confirm what shipped therefore confirmed nothing.

The revision is now a build argument baked into the image.  These tests fail if
anyone reintroduces a run-time override, because that override would win over
the baked value and put the drift back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "compose.yaml"
PYTHON_DOCKERFILE = "deploy/Dockerfile.python"
COMMIT_ENV = "KOR_TRAVEL_WEATHER_GIT_COMMIT"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _services_built_from_python_image(compose: dict) -> dict[str, dict]:
    return {
        name: service
        for name, service in compose["services"].items()
        if isinstance(service.get("build"), dict)
        and service["build"].get("dockerfile") == PYTHON_DOCKERFILE
    }


def test_no_service_injects_the_revision_at_run_time(compose: dict) -> None:
    offenders = [
        name
        for name, service in compose["services"].items()
        if COMMIT_ENV in (service.get("environment") or {})
    ]
    assert not offenders, (
        f"{offenders} inject {COMMIT_ENV} at run time; a run-time value overrides "
        "the one baked into the image and can drift from the deployed code"
    )


def test_every_python_image_receives_the_revision_as_a_build_arg(compose: dict) -> None:
    services = _services_built_from_python_image(compose)
    assert services, "expected at least one service built from the python image"
    for name, service in services.items():
        args = service["build"].get("args") or {}
        assert "GIT_COMMIT" in args, (
            f"service {name} builds {PYTHON_DOCKERFILE} without a GIT_COMMIT build "
            "arg, so its image cannot report the revision it contains"
        )


def test_the_python_image_bakes_the_revision(the_dockerfile: str) -> None:
    assert "ARG GIT_COMMIT" in the_dockerfile
    assert f"ENV {COMMIT_ENV}=${{GIT_COMMIT}}" in the_dockerfile


@pytest.fixture(scope="module")
def the_dockerfile() -> str:
    return (REPO_ROOT / PYTHON_DOCKERFILE).read_text(encoding="utf-8")


def test_the_unset_default_is_honest(the_dockerfile: str) -> None:
    """An unknown revision must read as unknown, not as something plausible.

    The previous default was the literal ``container``, which looks like an
    answer.  ``unknown`` cannot be mistaken for one.
    """
    assert "ARG GIT_COMMIT=unknown" in the_dockerfile


def test_the_example_env_does_not_pin_a_revision() -> None:
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert COMMIT_ENV not in example, (
        f"{COMMIT_ENV} in .env.example invites operators to pin a value by hand, "
        "which is how the deployed API came to report a stale commit"
    )
