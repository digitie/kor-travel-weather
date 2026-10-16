"""설치된 공용 CLI의 Weather liveness 계약과 배포 의존성을 확인한다."""

from __future__ import annotations

import importlib.metadata
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import tomllib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import grpc
import pytest
import yaml
from grpc_health.v1 import health_pb2, health_pb2_grpc

ROOT = Path(__file__).resolve().parents[1]
COMMON_COMMIT = "ad988a5dac252d710d41f9937b6d567c5d3398ae"
COMMAND = ["python", "-m", "kortravelcommon.grpc_health", "14106", "--check", "live"]

# SERVING이 성공하려면 CLI가 Dagster 없이 실행돼야 한다. 예외를 숨긴 경우도 exit1로 실패한다.
NO_DAGSTER_CLI = """
import importlib.abc, runpy, sys
class NoDagster(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'dagster' or fullname.startswith('dagster.'):
            raise AssertionError('healthcheck must not import Dagster')
sys.meta_path.insert(0, NoDagster())
sys.argv = ['kortravelcommon.grpc_health', *sys.argv[1:]]
runpy.run_module('kortravelcommon.grpc_health', run_name='__main__')
"""


def _cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", NO_DAGSTER_CLI, *args],
        capture_output=True, text=True, timeout=12, env=env,
    )


@contextmanager
def _health_server(status: int) -> Iterator[tuple[int, list[str]]]:
    requests: list[str] = []

    class Service(health_pb2_grpc.HealthServicer):
        def Check(self, request, context):  # noqa: N802
            requests.append(request.service)
            return health_pb2.HealthCheckResponse(status=status)

    with ThreadPoolExecutor(max_workers=1) as pool:
        server = grpc.server(pool)
        health_pb2_grpc.add_HealthServicer_to_server(Service(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        assert port > 0
        server.start()
        try:
            yield port, requests
        finally:
            server.stop(grace=None).wait(timeout=5)


def test_common_health_is_pinned_installed_and_scoped_to_dagster_extra() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    dagster_project = tomllib.loads(
        (ROOT / "packages/kor-travel-weather-dagster/pyproject.toml").read_text()
    )["project"]
    common = next(dep for dep in project["dependencies"] if dep.startswith("kor-travel-common"))
    assert common.startswith("kor-travel-common @") and COMMON_COMMIT in common
    extras = project["optional-dependencies"]["dagster"]
    assert any(dep.startswith("kor-travel-common[grpc-health] @") and COMMON_COMMIT in dep
               for dep in extras)
    assert any(dep.startswith("kor-travel-common[dagster,grpc-health] @")
               and COMMON_COMMIT in dep for dep in dagster_project["dependencies"])
    locked = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    package = next(dep for dep in locked if dep["name"] == "kor-travel-common")
    assert COMMON_COMMIT in package["source"]["git"]
    assert {item["name"] for item in package["optional-dependencies"]["grpc-health"]} == {
        "grpcio", "grpcio-health-checking", "protobuf",
    }
    distribution = importlib.metadata.distribution("kor-travel-common")
    direct = json.loads(distribution.read_text("direct_url.json"))
    assert direct["vcs_info"]["commit_id"] == COMMON_COMMIT


def test_code_server_health_is_liveness_without_changing_monitoring() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    check = compose["services"]["dagster-code-server"]["healthcheck"]
    assert check == {
        "test": ["CMD", *COMMAND], "interval": "30s", "timeout": "10s",
        "retries": 5, "start_period": "30s",
    }
    instance = yaml.safe_load((ROOT / "deploy/dagster.yaml").read_text())
    assert instance["run_monitoring"]["enabled"] is True
    assert instance["run_retries"]["enabled"] is True


@pytest.mark.parametrize("status, expected", [
    (health_pb2.HealthCheckResponse.SERVING, 0),
    (health_pb2.HealthCheckResponse.NOT_SERVING, 1),
])
def test_installed_cli_is_quiet_single_check_without_dagster(status: int, expected: int) -> None:
    with _health_server(status) as (port, requests):
        result = _cli(str(port), "--check", "live")
    assert result.returncode == expected and result.stdout == result.stderr == ""
    # ListRepositories와 provider/DB는 이 가짜 서버에 구현하지 않았다.
    assert requests == ["DagsterApi"]


def test_installed_liveness_ignores_proxy_environment() -> None:
    proxy_connections: list[bool] = []

    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            proxy_connections.append(True)

    with socketserver.TCPServer(("127.0.0.1", 0), Proxy) as proxy:
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            env = os.environ.copy()
            proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
            for name in ["grpc_proxy", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
                env[name] = proxy_url
            env.update(no_proxy="", NO_PROXY="", no_grpc_proxy="")
            with _health_server(health_pb2.HealthCheckResponse.SERVING) as (port, requests):
                result = _cli(str(port), "--check", "live", env=env)
            assert result.returncode == 0 and result.stdout == result.stderr == ""
            assert requests == ["DagsterApi"] and not proxy_connections
        finally:
            proxy.shutdown()
            thread.join(timeout=5)


def test_installed_closed_port_is_quiet_failure() -> None:
    # bind만 유지해 다른 서비스가 같은 포트를 점유하지 못하게 한다.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        result = _cli(str(reserved.getsockname()[1]), "--check", "live")
    assert result.returncode == 1 and result.stdout == result.stderr == ""


def test_installed_invalid_arguments_are_quiet_usage_failure() -> None:
    result = _cli("14106", "--check", "ready")
    assert result.returncode == 2 and result.stdout == result.stderr == ""
