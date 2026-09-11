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
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = REPO_ROOT / "compose.yaml"
N150_COMPOSE = REPO_ROOT / "deploy" / "compose.n150.yaml"
#: Every compose file in the repository, found rather than listed.  Naming
#: them is how the last round of this test missed the override file.
COMPOSE_FILES = sorted(
    {BASE_COMPOSE, *REPO_ROOT.glob("compose*.y*ml"), *REPO_ROOT.glob("deploy/compose*.y*ml")}
)
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
    return {
        str(path.relative_to(REPO_ROOT)).replace("\\", "/"): _load(path)
        for path in COMPOSE_FILES
    }


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


def test_no_compose_file_pins_the_build_arg_to_a_literal(
    compose_files: dict[str, dict],
) -> None:
    """A literal is the hand-pinned value this change removed, wherever it sits.

    An override file wins over the base, so checking only the base leaves the
    production-only file free to bake a fixed revision into every image.
    """
    offenders: list[str] = []
    for filename, document in compose_files.items():
        for name, service in (document.get("services") or {}).items():
            build = service.get("build")
            if not isinstance(build, dict):
                continue
            value = (build.get("args") or {}).get(BUILD_ARG)
            if value is None:
                continue
            if not str(value).startswith("${"):
                offenders.append(f"{filename}:{name}={value!r}")
    assert not offenders, (
        f"{offenders} pin {BUILD_ARG} to a literal; it would be baked into every "
        "image forever, which is exactly the failure this change removed"
    )


def test_the_build_arg_reads_the_name_the_runbooks_export(
    compose_files: dict[str, dict],
) -> None:
    """Bind the interpolation to the variable the deploy commands actually set.

    Renaming one side leaves the other silently supplying nothing, and every
    image then bakes ``unknown`` while all the other checks still pass.
    """
    for name, service in _python_image_services(compose_files["compose.yaml"]).items():
        value = str(service["build"]["args"][BUILD_ARG])
        assert value.startswith("${" + BUILD_ARG), (
            f"service {name} interpolates {value!r}, which is not the {BUILD_ARG} "
            "the documented build commands export"
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
    # Exactly one of each: Docker takes the last declaration, so a second line
    # appended below would silently win and could bake a fixed revision while a
    # "check the first one" test stayed green.
    assert len(arg_lines) == 1, (
        f"expected one active `ARG {BUILD_ARG}` line, found {len(arg_lines)}; "
        "the last declaration wins, so a duplicate can override the intended one"
    )
    assert len(env_lines) == 1, (
        f"expected one active `ENV {COMMIT_ENV}=` line, found {len(env_lines)}; "
        "the last declaration wins"
    )
    assert arg_lines[0] > final_from, (
        f"ARG {BUILD_ARG} is declared before the final FROM, so it is out of scope "
        "in the stage that ships and the image would carry no revision"
    )
    assert env_lines[0] > arg_lines[0], "ENV must follow the ARG it reads"
    assert dockerfile_lines[env_lines[0]].strip() == f"ENV {COMMIT_ENV}=${{{BUILD_ARG}}}"


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


def test_every_documented_build_command_computes_the_revision() -> None:
    """Find the build commands rather than listing the documents that hold them.

    The same invocation is copied across several runbooks; fixing the ones you
    remember leaves the rest baking ``unknown``, and one of them then tells the
    operator to smoke-check ``/version``. Listing the documents is what let that
    happen, so search instead.

    Requiring the value to be *computed* matters as much as its presence: a
    typed-in ``GIT_COMMIT=6003da9`` satisfies a substring check and is precisely
    the hand-pinned revision this change exists to remove.
    """
    computed = re.compile(rf"{BUILD_ARG}=\$\((?:git|`)")
    offenders: list[str] = []
    for document in sorted(REPO_ROOT.glob("**/*.md")):
        if any(part in {".git", "node_modules", ".venv"} for part in document.parts):
            continue
        lines = document.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "up -d --build" not in line:
                continue
            # A shell invocation may be wrapped, so walk back over continuations
            # to find where the command actually starts.
            start = index
            while start > 0 and lines[start - 1].rstrip().endswith("\\"):
                start -= 1
            command = " ".join(lines[start : index + 1])
            if not computed.search(command):
                offenders.append(f"{document.relative_to(REPO_ROOT)}:{index + 1}")
    assert not offenders, (
        f"{offenders} build without computing {BUILD_ARG} from git; those images "
        "report a revision that is either unknown or hand-typed, while the "
        "surrounding runbook says to verify /version"
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


def test_every_provider_credential_reaches_the_containers() -> None:
    """A setting the app reads is useless if compose does not pass it in.

    Compose forwards only what a service names in ``environment``, so adding a
    credential field to settings is two changes, in two files, and nothing
    connected them. The gap is silent in exactly the wrong way: the deployment
    has the key in ``.env``, the container does not, and the run fails with
    "credential이 설정되지 않았습니다" -- which reads like a missing key rather
    than a missing wire. That is what happened to `python-krex-api` on its
    first enabled run.
    """
    from kortravelweather.providers import PROVIDER_CATALOG

    required = {
        f"KOR_TRAVEL_WEATHER_{spec.credential_field.upper()}"
        for spec in PROVIDER_CATALOG
        if spec.auth_required and spec.credential_field
    }
    # KMA is built by the Dagster resource from the shared data.go.kr key, and
    # the two run in these services; anything else that authenticates has to be
    # reachable from both.
    for service_name in ("api", "dagster"):
        names: set[str] = set()
        for path in COMPOSE_FILES:
            service = (_load(path).get("services") or {}).get(service_name)
            if service:
                names |= _environment_names(service)
        missing = sorted(required - names)
        assert not missing, (
            f"{service_name} does not receive {missing}; the setting exists and "
            "the deployment can set it, but the container never sees it"
        )


def test_dagster_runs_with_run_monitoring_enabled() -> None:
    """A run whose process dies must not hold a concurrency slot for ever.

    Dagster's default is to leave it STARTED. It keeps counting against
    `max_concurrent_runs`, so after enough container replacements the limit is
    full of runs that are not running and nothing new launches -- twice in one
    deployment week, the second time stalling hourly collection for eighteen
    hours. `run_monitoring` is what releases the slot without a human.
    """
    config_path = REPO_ROOT / "deploy" / "dagster.yaml"
    assert config_path.exists(), "deploy/dagster.yaml is missing"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config.get("run_monitoring", {}).get("enabled") is True

    mounted = False
    for path in COMPOSE_FILES:
        service = (_load(path).get("services") or {}).get("dagster")
        for volume in (service or {}).get("volumes") or []:
            if "dagster.yaml" in str(volume):
                mounted = True
    assert mounted, (
        "deploy/dagster.yaml exists but no compose file mounts it, so the "
        "container still runs on Dagster's defaults"
    )


def test_dagster_runs_are_recorded_in_postgresql_not_sqlite() -> None:
    """SQLite accepts one writer; ten concurrent runs plus the daemon are ten.

    Without an explicit ``run_storage``/``event_log_storage``/
    ``schedule_storage``, Dagster falls back to SQLite files under
    ``DAGSTER_HOME``, and concurrent writers hit
    ``sqlite3.OperationalError: database is locked``. A run whose
    STEP_SUCCESS/RUN_SUCCESS event loses that race never updates its own
    status: it stays STARTED with a live process, which is a state
    ``run_monitoring`` (see ``test_dagster_runs_with_run_monitoring_enabled``)
    has no reason to touch, so the slot it holds is never released. PostgreSQL
    handles concurrent writers correctly, which is why it is the officially
    supported alternative.
    """
    config_path = REPO_ROOT / "deploy" / "dagster.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for key, class_name in (
        ("run_storage", "PostgresRunStorage"),
        ("event_log_storage", "PostgresEventLogStorage"),
        ("schedule_storage", "PostgresScheduleStorage"),
    ):
        section = config.get(key) or {}
        assert section.get("module", "").startswith("dagster_postgres"), (
            f"{key} is not backed by dagster_postgres, so it is on Dagster's "
            f"SQLite default: {section}"
        )
        assert section.get("class") == class_name, section
        assert "env" in (section.get("config", {}).get("postgres_url") or {}), (
            f"{key}'s postgres_url is not sourced from an environment "
            "variable, so this test cannot confirm the container receives it"
        )

    names: set[str] = set()
    for path in COMPOSE_FILES:
        service = (_load(path).get("services") or {}).get("dagster")
        if service:
            names |= _environment_names(service)
    assert "DAGSTER_POSTGRES_URL" in names, (
        "deploy/dagster.yaml points run/event-log/schedule storage at "
        "DAGSTER_POSTGRES_URL, but no compose file passes that variable to "
        "the dagster service"
    )


def test_the_web_image_copies_its_public_directory_into_the_runtime_stage() -> None:
    """``next start`` serves static files from ``./public``, relative to its cwd.

    The admin frontend's ``public/`` directory was empty until
    ``scripts/copy-maplibre-worker.mjs`` (a build-time ``prebuild``/``predev``
    hook) started writing maplibre-gl's worker files into it, so the runtime
    stage never needed to copy it and the omission was invisible. It built
    clean and ran clean locally -- `npm run build && npm run start` from one
    working directory never notices a copy the image itself never performs --
    and would have shipped a container where the map mounts with no tiles,
    silently, because the worker 404s. Next.js's own convention is that
    ``public/`` is always required at runtime when it exists in the source
    tree; the runner stage must copy it unconditionally, not only once
    something is known to depend on it.
    """
    dockerfile = (REPO_ROOT / "deploy" / "Dockerfile.web").read_text(encoding="utf-8")
    stages = re.split(r"(?im)^FROM\b", dockerfile)
    runner_stage = next(
        (stage for stage in stages if re.search(r"(?i)\bAS\s+runner\b", stage)), None
    )
    assert runner_stage is not None, "no 'AS runner' stage found in Dockerfile.web"
    assert re.search(
        r"COPY\s+--from=builder\s+/app/public\s+\./public", runner_stage
    ), "the runner stage does not copy /app/public from the builder stage"
