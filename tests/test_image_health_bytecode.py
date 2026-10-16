"""공유 Python 이미지의 cache 준비와 경량 API 점검을 검증한다."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILD = ["-I", "-m", "kortravelcommon.build_bytecode", "--stdlib"]
HTTP = [
    "CMD", "python", "-I", "-m", "kortravelcommon.http_health",
    "--port", "14101", "--path", "/health", "--timeout-seconds", "3",
]


def test_shared_final_interpreter_compiles_once_and_propagates_failure(tmp_path: Path) -> None:
    dockerfile = (ROOT / "deploy/Dockerfile.python").read_text()
    lines = re.sub(r"\\\n", " ", dockerfile).splitlines()
    instructions = [
        (match[1], match[2]) for line in lines
        if (match := re.match(r"^(FROM|RUN|COPY|USER|HEALTHCHECK)\s+(.*)", line))
    ]
    builds = [(i, value) for i, (kind, value) in enumerate(instructions)
              if kind == "RUN" and "kortravelcommon.build_bytecode" in value]
    assert len(builds) == 1
    index, command = builds[0]
    assert shlex.split(command) == ["python", *BUILD]
    final_stage = max(i for i, (kind, _) in enumerate(instructions) if kind == "FROM")
    assert final_stage < index
    assert any(kind == "RUN" and "uv sync --locked --no-dev" in value
               for kind, value in instructions[final_stage:index])
    assert not any(kind == "USER" for kind, _ in instructions[final_stage:index])
    assert not any(kind == "COPY" for kind, _ in instructions[index + 1:])
    # 공용 image에 API 전용 기본 HC를 넣어 코드서버의 계약을 혼합하지 않는다.
    assert not any(kind == "HEALTHCHECK" for kind, _ in instructions)
    probe = tmp_path / "python"
    output = tmp_path / "argv.json"
    probe.write_text(
        "#!" + sys.executable + "\nimport json,os,sys; "
        "open(os.environ['ARGV_OUT'],'w').write(json.dumps(sys.argv[1:])); "
        "sys.exit(int(os.environ['PROBE_EXIT']))\n"
    )
    probe.chmod(0o755)
    for code in (0, 1):
        result = subprocess.run(
            ["/bin/sh", "-c", command],
            env={**os.environ, "PATH": str(tmp_path), "ARGV_OUT": str(output),
                 "PROBE_EXIT": str(code)}, check=False,
        )
        assert result.returncode == code
        assert json.loads(output.read_text()) == BUILD


def test_installed_builder_writes_checked_hash_only_in_tiny_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kortravelcommon import build_bytecode

    source = tmp_path / "sample.py"
    source.write_text("VALUE = 42\n")
    excluded = tmp_path / "site-packages" / "external.py"
    excluded.parent.mkdir()
    excluded.write_text("raise RuntimeError('must not compile')\n")
    monkeypatch.setattr(build_bytecode.sysconfig, "get_path", lambda _: str(tmp_path))
    assert build_bytecode.main(["--stdlib"]) == 0
    cache = Path(importlib.util.cache_from_source(str(source), optimization=""))
    assert int.from_bytes(cache.read_bytes()[4:8], "little") == 3
    assert cache.read_bytes()[8:16] == importlib.util.source_hash(source.read_bytes())
    assert not Path(importlib.util.cache_from_source(str(excluded))).exists()
    source.write_text("invalid syntax !\n")
    assert build_bytecode.main(["--stdlib"]) == 1


def test_production_health_intervals_tripled_without_probe_budget_changes() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    expected = {
        "db": ("15s", "5s", 12, None),
        "api": ("30s", "5s", 12, None),
        "dagster-code-server": ("90s", "10s", 5, "30s"),
        "dagster-webserver": ("45s", "5s", 8, "30s"),
        "dagster-daemon": ("90s", "10s", 5, "60s"),
        "prometheus": ("30s", "5s", 12, None),
    }
    assert {name for name, value in compose.items() if "healthcheck" in value} == set(expected)
    for name, (interval, timeout, retries, start) in expected.items():
        check = compose[name]["healthcheck"]
        assert (check["interval"], check["timeout"], check["retries"],
                check.get("start_period")) == (interval, timeout, retries, start)
    assert compose["api"]["healthcheck"]["test"] == HTTP
    assert compose["api"]["command"][-2:] == ["--port", "14101"]
    assert compose["dagster-daemon"]["healthcheck"]["test"] == [
        "CMD", "dagster-daemon", "liveness-check",
    ]


@pytest.mark.parametrize(("reply", "expected"), [
    (b"HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\n", 0),
    (b"HTTP/1.0 302 Found\r\nLocation: /health\r\n\r\n", 1),
    (b"HTTP/1.0 503 Error\r\n\r\n", 1),
    (b"HTTP/1.0 200\r\n\r\n", 1),
    (b"HTTP/1.0 200 OK\r\n", 1),
])
def test_installed_http_cli_is_quiet_single_header_probe_without_heavy_import(
    reply: bytes, expected: int,
) -> None:
    seen: list[bytes] = []
    source = """
import importlib.abc, runpy, sys
class NoHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'dagster', 'grpc', 'sqlalchemy', 'kortravelweather'}:
            raise AssertionError('health imported application or optional dependencies')
sys.meta_path.insert(0, NoHeavy())
sys.argv = ['kortravelcommon.http_health', '--port', sys.argv[1],
            '--path', '/health', '--timeout-seconds', '0.3']
runpy.run_module('kortravelcommon.http_health', run_name='__main__')
"""
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as pool:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(4)

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(1024)
                    assert chunk
                    request += chunk
                    assert len(request) <= 16_384
                seen.append(request)
                connection.sendall(reply)

        future = pool.submit(serve)
        result = subprocess.run(
            [sys.executable, "-I", "-c", source, str(listener.getsockname()[1])],
            env={**os.environ, "HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""},
            capture_output=True, text=True, timeout=5, check=False,
        )
        future.result(timeout=5)
    assert result.returncode == expected
    assert result.stdout == result.stderr == ""
    assert len(seen) == 1 and seen[0].split(b"\r\n", 1)[0] == b"GET /health HTTP/1.0"


def test_installed_http_cli_closed_port_is_quiet_failure() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        result = subprocess.run(
            [sys.executable, "-I", "-m", "kortravelcommon.http_health",
             "--port", str(listener.getsockname()[1]), "--timeout-seconds", "0.1"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    assert result.returncode == 1 and result.stdout == result.stderr == ""
