from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from prometheus_client.mmap_dict import MmapedDict

from kortravelweather.metrics import (
    metrics_content_type,
    metrics_payload,
    observe_http_request,
    observe_provider_request,
)
from kortravelweather.settings import WeatherSettings


def _metrics_client() -> tuple[TestClient, str, str]:
    admin_token = "admin-token-for-metrics-tests-1234"
    metrics_token = "metrics-token-for-scrape-tests-5678"
    settings = WeatherSettings(
        _env_file=None,
        environment="production",
        database_url="postgresql+psycopg://weather@127.0.0.1:15432/weather_test",
        admin_token=admin_token,
        metrics_token=metrics_token,
    )
    # Liveness and metrics do not touch the repository.  Keeping this test
    # repository-free makes the scrape contract runnable without PostgreSQL.
    return TestClient(create_app(settings, repository=object())), admin_token, metrics_token


def test_metrics_endpoint_is_authenticated_and_not_cached() -> None:
    client, admin_token, metrics_token = _metrics_client()

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"x-admin-token": admin_token}).status_code == 401

    response = client.get(
        "/health",
        headers={"x-request-id": "request-id-must-not-be-a-label"},
    )
    assert response.status_code == 200
    scraped = client.get("/metrics", headers={"authorization": f"Bearer {metrics_token}"})
    assert scraped.status_code == 200
    assert scraped.headers["content-type"] == metrics_content_type()
    assert scraped.headers["cache-control"] == "no-store, private"
    assert scraped.headers["x-content-type-options"] == "nosniff"
    assert "ktw_http_requests_total" in scraped.text
    assert "kor_travel_weather_" not in scraped.text
    assert 'route="/health"' in scraped.text
    assert "request-id-must-not-be-a-label" not in scraped.text
    # Scraping itself is deliberately excluded from the HTTP request counter.
    assert 'route="/metrics"' not in scraped.text


def test_metrics_labels_collapse_unregistered_provider_and_dataset() -> None:
    observe_provider_request(
        "attacker-controlled-provider-1234567890",
        "attacker-controlled-dataset-1234567890",
        outcome="success",
        duration_seconds=0.001,
    )
    text = metrics_payload().decode("utf-8")
    assert 'dataset="other",outcome="success",provider="other"' in text
    assert "attacker-controlled-provider" not in text
    assert "attacker-controlled-dataset" not in text


def test_metrics_http_method_label_is_bounded() -> None:
    observe_http_request(
        method="x-random-method-that-must-not-be-a-series",
        route="/v1/weather/locations",
        status_code=405,
        duration_seconds=0.001,
    )
    text = metrics_payload().decode("utf-8")
    assert 'method="other"' in text
    assert "x-random-method-that-must-not-be-a-series" not in text
    assert 'route="/v1/weather/locations"' in text
    observe_http_request(
        method="GET",
        route="/v1/weather/locations/attacker-controlled-id",
        status_code=404,
        duration_seconds=0.001,
    )
    text = metrics_payload().decode("utf-8")
    assert 'route="unmatched"' in text
    assert "attacker-controlled-id" not in text


def test_production_requires_a_dedicated_metrics_token() -> None:
    settings = WeatherSettings(
        _env_file=None,
        environment="production",
        database_url="postgresql+psycopg://weather@127.0.0.1:15432/weather_test",
        admin_token="admin-token-for-metrics-tests-1234",
    )
    with pytest.raises(RuntimeError, match="METRICS_TOKEN"):
        create_app(settings, repository=object())


def test_multiprocess_registry_aggregates_worker_samples(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    worker = (
        "from kortravelweather.metrics import observe_provider_request; "
        "observe_provider_request('weatherapi', 'weatherapi_current', "
        "outcome='success', duration_seconds=0.001)"
    )
    for _ in range(2):
        subprocess.run([sys.executable, "-c", worker], env=environment, check=True)
    scraper = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kortravelweather.metrics import metrics_payload; "
            "print(metrics_payload().decode())",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        'ktw_provider_requests_total{dataset="weatherapi_current",'
        'outcome="success",provider="weatherapi"} 2.0'
    ) in scraper.stdout


def test_multiprocess_live_gauge_is_cleaned_on_sigterm(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    worker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal; "
            "from kortravelweather.metrics import observe_sync_started; "
            "observe_sync_started('weatherapi', 'weatherapi_current'); signal.pause()",
        ],
        env=environment,
    )
    try:
        for _ in range(100):
            if any(tmp_path.iterdir()):
                break
            time.sleep(0.01)
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=5)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
    scraper = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kortravelweather.metrics import metrics_payload; "
            "print(metrics_payload().decode())",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert 'ktw_sync_runs_active{dataset="weatherapi_current"' not in (
        scraper.stdout
    )


def test_multiprocess_live_gauge_is_cleaned_after_worker_is_killed(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    worker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os; "
            "from kortravelweather.metrics import observe_sync_started; "
            "observe_sync_started('weatherapi', 'weatherapi_current'); os._exit(0)",
        ],
        env=environment,
    )
    worker.wait(timeout=5)
    assert any(tmp_path.glob("gauge_live*.db"))

    scraper = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kortravelweather.metrics import metrics_payload; "
            "print(metrics_payload().decode())",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert 'ktw_sync_runs_active{dataset="weatherapi_current"' not in (
        scraper.stdout
    )
    assert not any(tmp_path.glob("gauge_live*.db"))


def test_corrupt_multiprocess_gauge_filename_does_not_break_scrape(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    corrupt_file = tmp_path / f"gauge_livesum_{'9' * 220}.db"
    corrupt_file.touch()
    scraper = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kortravelweather.metrics import metrics_payload; "
            "print(metrics_payload().decode())",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert scraper.stdout
    assert not corrupt_file.exists()


def test_malformed_multiprocess_gauge_file_does_not_break_scrape(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    for name in ("gauge_livesum_bad.db", "gauge_livesum_-1.db", "gauge_livesum_999999999.db"):
        (tmp_path / name).touch()
    scraper = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kortravelweather.metrics import metrics_payload; "
            "print(metrics_payload().decode())",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert scraper.stdout
    assert not list(tmp_path.glob("gauge_livesum_*.db"))


def test_malformed_multiprocess_file_does_not_hide_valid_series(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    worker = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kortravelweather.metrics import observe_provider_request; "
            "observe_provider_request('weatherapi', 'weatherapi_current', "
            "outcome='success', duration_seconds=0.001)",
        ],
        env=environment,
        check=True,
    )
    assert worker.returncode == 0
    corrupt_file = tmp_path / "counter_999999999.db"
    corrupt_mmap = MmapedDict(str(corrupt_file))
    corrupt_mmap.write_value("null", 1.0, 0.0)
    corrupt_mmap.close()
    malformed_gauge = tmp_path / "gauge_bad.db"
    malformed_mmap = MmapedDict(str(malformed_gauge))
    malformed_mmap.write_value('["bad_metric", "bad_metric", {}, "help"]', 1.0, 0.0)
    malformed_mmap.close()
    scraper = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kortravelweather.metrics import metrics_payload; "
            "print(metrics_payload().decode())",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert 'ktw_provider_requests_total{dataset="weatherapi_current"' in (
        scraper.stdout
    )
    assert not corrupt_file.exists()
    assert not malformed_gauge.exists()


def test_multiprocess_http_listener_cleans_after_worker_is_killed(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal; "
            "from kortravelweather.metrics import start_metrics_server; "
            f"start_metrics_server({port}, address='127.0.0.1'); "
            "print('ready', flush=True); signal.pause()",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        text=True,
    )
    worker = None
    try:
        assert listener.stdout is not None
        assert listener.stdout.readline().strip() == "ready"
        worker = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal; "
                "from kortravelweather.metrics import observe_sync_started; "
                "observe_sync_started('weatherapi', 'weatherapi_current'); "
                "print('ready', flush=True); signal.pause()",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert worker.stdout is not None
        assert worker.stdout.readline().strip() == "ready"
        worker_gauge = tmp_path / f"gauge_livesum_{worker.pid}.db"
        assert worker_gauge.exists()
        worker.kill()
        worker.wait(timeout=5)
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert 'ktw_sync_runs_active{dataset="weatherapi_current"' not in body
        assert not worker_gauge.exists()
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        listener.terminate()
        listener.wait(timeout=5)
