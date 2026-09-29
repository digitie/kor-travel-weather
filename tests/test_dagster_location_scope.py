"""Bind the admin's Dagster scope to where the code location is actually named.

The admin UI restricts every Dagster query to this project's code location
(``lib/dagster-scope.ts``), so a Dagster webserver shared with other projects
shows it only its own repository and runs.  That only works while the name it
filters on is the name the location is served under.  The name is written in
deploy/workspace.yaml and follows from the code server's ``-m`` module; a
rename there that missed the TypeScript copy would make the admin page empty
(a selector for a location that does not exist), not wrong -- which is exactly
the kind of failure nobody notices for a while.  These tests make it loud.

They also bind the per-run ``dagster/max_runtime`` tag to the instance-wide
bound it replaces on a shared instance.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT / "deploy" / "workspace.yaml"
INSTANCE = REPO_ROOT / "deploy" / "dagster.yaml"
COMPOSE = REPO_ROOT / "compose.yaml"
FRONTEND = REPO_ROOT / "packages" / "kor-travel-weather-admin" / "frontend"
SCOPE_MODULE = FRONTEND / "lib" / "dagster-scope.ts"
DAGSTER_SRC = REPO_ROOT / "packages" / "kor-travel-weather-dagster" / "src"
UNSCOPED_ROOT_FIELDS = re.compile(
    r"\b(repositoriesOrError|workspaceOrError|runsOrError|runOrError)\s*[({]"
)


def _workspace_locations() -> list[str]:
    workspace = yaml.safe_load(WORKSPACE.read_text(encoding="utf-8"))
    return [entry["grpc_server"]["location_name"] for entry in workspace["load_from"]]


def _scope_location() -> str:
    text = SCOPE_MODULE.read_text(encoding="utf-8")
    matches = re.findall(r'export const DAGSTER_LOCATION_NAME = "([^"]+)";', text)
    assert len(matches) == 1, "lib/dagster-scope.ts must define DAGSTER_LOCATION_NAME exactly once"
    return matches[0]


def _code_server_module() -> str:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    command = compose["services"]["dagster-code-server"]["command"]
    return command[command.index("-m") + 1]


def test_workspace_serves_exactly_one_location() -> None:
    assert len(_workspace_locations()) == 1


def test_the_admin_scope_names_the_served_location() -> None:
    assert _scope_location() == _workspace_locations()[0]


def test_the_location_is_the_code_server_module() -> None:
    module = _code_server_module()
    assert _workspace_locations()[0] == module
    assert (DAGSTER_SRC / (module.replace(".", "/") + ".py")).is_file(), module


def test_no_frontend_code_sends_unscoped_dagster_queries() -> None:
    """Every GraphQL document lives in the scope module, and nowhere else.

    A second query document elsewhere in the admin would bypass the proxy's
    allowlist only if the proxy forwarded it -- it does not -- but it would
    also be dead code that reads like a working query. Fail on it.
    """
    offenders = []
    for path in FRONTEND.rglob("*.ts*"):
        if "node_modules" in path.parts or ".next" in path.parts or path == SCOPE_MODULE:
            continue
        if path.name.endswith(".test.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        if UNSCOPED_ROOT_FIELDS.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"Dagster GraphQL documents outside lib/dagster-scope.ts: {offenders}"


def test_the_run_tag_keeps_the_instance_bound() -> None:
    pytest.importorskip("dagster")
    import sys

    sys.path.insert(0, str(DAGSTER_SRC))
    from kortravelweather_dagster.definitions import RUN_MAX_RUNTIME_SECONDS

    instance = yaml.safe_load(INSTANCE.read_text(encoding="utf-8"))
    assert instance["run_monitoring"]["max_runtime_seconds"] == RUN_MAX_RUNTIME_SECONDS
