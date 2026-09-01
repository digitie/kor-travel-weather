"""Low-cardinality Prometheus instrumentation shared by API and workers.

The weather catalog contains user-controlled identifiers (location ids, run
ids, source keys and provider URLs).  None of those values are ever labels in
this module.  Provider and dataset labels are reduced to a small catalog
allow-list so a malformed request cannot turn the metrics endpoint into an
unbounded in-memory store.
"""

from __future__ import annotations

import atexit
import os
import re
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from threading import Lock
from time import perf_counter

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
    start_http_server,
)
from prometheus_client.multiprocess import MultiProcessCollector

_MULTIPROCESS_DIR = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
_LIVE_GAUGE_FILE = re.compile(
    r"^gauge_live(?:min|max|sum|mostrecent|all)_(?P<pid>[0-9]+)\.db$"
)


def cleanup_dead_multiprocess_gauges() -> int:
    """Remove live-gauge files whose writer process no longer exists.

    ``atexit``/signal hooks cannot run after SIGKILL or an OOM kill.  The
    multiprocess collector would otherwise keep those samples forever.  A
    scrape-time liveness check is safe because only the live gauge files are
    touched and files owned by a still-running PID are left alone.
    """
    if not _MULTIPROCESS_DIR:
        return 0
    removed = 0
    try:
        entries = list(os.scandir(_MULTIPROCESS_DIR))
    except OSError:
        return 0
    for entry in entries:
        match = _LIVE_GAUGE_FILE.fullmatch(entry.name)
        if match is None:
            continue
        try:
            pid = int(match.group("pid"))
        except ValueError:
            # A corrupted filename must not make the scrape endpoint fail.
            with suppress(FileNotFoundError, OSError):
                os.unlink(entry.path)
                removed += 1
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            with suppress(FileNotFoundError, OSError):
                os.unlink(entry.path)
                removed += 1
        except OverflowError:
            # The value cannot be a PID on this host.  Remove the invalid
            # file so the collector does not try to parse it below.
            with suppress(FileNotFoundError, OSError):
                os.unlink(entry.path)
                removed += 1
            continue
        except (PermissionError, OSError):
            # A permission error means the process may still be alive.  Do
            # not delete a gauge we cannot positively identify as stale.
            continue
    return removed


def _registries() -> tuple[CollectorRegistry, CollectorRegistry]:
    """Build scrape/instrumentation registries with Dagster multiprocess support."""
    multiprocess_dir = _MULTIPROCESS_DIR
    if not multiprocess_dir:
        registry = CollectorRegistry(auto_describe=True)
        return registry, registry
    if not os.path.isdir(multiprocess_dir):
        raise RuntimeError(
            "PROMETHEUS_MULTIPROC_DIR가 존재하는 디렉터리를 가리켜야 합니다."
        )
    cleanup_dead_multiprocess_gauges()
    scrape_registry = CollectorRegistry(auto_describe=True)
    MultiProcessCollector(scrape_registry, path=multiprocess_dir)
    instrumentation_registry = CollectorRegistry(auto_describe=True)
    return scrape_registry, instrumentation_registry


REGISTRY, _INSTRUMENTATION_REGISTRY = _registries()


def _cleanup_multiprocess_gauges() -> None:
    """Remove this process's live gauges during normal worker shutdown."""
    if _MULTIPROCESS_DIR:
        with suppress(Exception):
            multiprocess.mark_process_dead(os.getpid())


if _MULTIPROCESS_DIR:
    atexit.register(_cleanup_multiprocess_gauges)

    _previous_signal_handlers: dict[int, object] = {}

    def _cleanup_on_signal(signum: int, frame: object) -> None:
        _cleanup_multiprocess_gauges()
        previous = _previous_signal_handlers.get(signum, signal.SIG_DFL)
        if previous is signal.SIG_IGN:
            return
        if callable(previous):
            previous(signum, frame)
            return
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    for _signal_number in (signal.SIGTERM, signal.SIGINT):
        try:
            _previous_signal_handlers[_signal_number] = signal.getsignal(_signal_number)
            signal.signal(_signal_number, _cleanup_on_signal)
        except (OSError, RuntimeError, ValueError):
            # Worker imports can occur outside the main thread.  Normal
            # atexit cleanup still applies in that case.
            pass

HTTP_REQUESTS = Counter(
    "kor_travel_weather_http_requests_total",
    "HTTP requests observed by the weather API.",
    ("method", "route", "status_class"),
    registry=_INSTRUMENTATION_REGISTRY,
)
HTTP_DURATION = Histogram(
    "kor_travel_weather_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=_INSTRUMENTATION_REGISTRY,
)
HTTP_IN_FLIGHT = Gauge(
    "kor_travel_weather_http_requests_in_flight",
    "HTTP requests currently being processed.",
    ("method",),
    multiprocess_mode="livesum",
    registry=_INSTRUMENTATION_REGISTRY,
)

PROVIDER_REQUESTS = Counter(
    "kor_travel_weather_provider_requests_total",
    "Logical provider requests performed by a sync worker.",
    ("provider", "dataset", "outcome"),
    registry=_INSTRUMENTATION_REGISTRY,
)
PROVIDER_DURATION = Histogram(
    "kor_travel_weather_provider_request_duration_seconds",
    "Provider request duration in seconds.",
    ("provider", "dataset"),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=_INSTRUMENTATION_REGISTRY,
)

SYNC_STARTED = Counter(
    "kor_travel_weather_sync_runs_started_total",
    "Weather sync runs successfully opened.",
    ("provider", "dataset"),
    registry=_INSTRUMENTATION_REGISTRY,
)
SYNC_FINISHED = Counter(
    "kor_travel_weather_sync_runs_finished_total",
    "Weather sync runs terminalized by status.",
    ("provider", "dataset", "status"),
    registry=_INSTRUMENTATION_REGISTRY,
)
SYNC_ACTIVE = Gauge(
    "kor_travel_weather_sync_runs_active",
    "Currently running weather sync runs.",
    ("provider", "dataset"),
    multiprocess_mode="livesum",
    registry=_INSTRUMENTATION_REGISTRY,
)
SYNC_REQUESTS = Counter(
    "kor_travel_weather_sync_requests_total",
    "Provider requests attributed to completed sync runs.",
    ("provider", "dataset"),
    registry=_INSTRUMENTATION_REGISTRY,
)
SYNC_SOURCES = Counter(
    "kor_travel_weather_sync_source_records_total",
    "Immutable source records published by completed sync runs.",
    ("provider", "dataset"),
    registry=_INSTRUMENTATION_REGISTRY,
)
SYNC_VALUES = Counter(
    "kor_travel_weather_sync_values_total",
    "Normalized weather facts published by completed sync runs.",
    ("provider", "dataset"),
    registry=_INSTRUMENTATION_REGISTRY,
)
SYNC_STALE_RECOVERED = Counter(
    "kor_travel_weather_sync_stale_recovered_total",
    "Running sync rows recovered after a worker interruption.",
    registry=_INSTRUMENTATION_REGISTRY,
)
METRIC_ERRORS = Counter(
    "kor_travel_weather_metrics_errors_total",
    "Instrumentation errors swallowed to keep the data path healthy.",
    ("operation",),
    registry=_INSTRUMENTATION_REGISTRY,
)
METRICS_SERVER_UP = Gauge(
    "kor_travel_weather_metrics_server_up",
    "Whether this process successfully bound its worker metrics listener.",
    multiprocess_mode="livemax",
    registry=_INSTRUMENTATION_REGISTRY,
)
METRICS_SERVER_BIND_FAILURES = Counter(
    "kor_travel_weather_metrics_server_bind_failures_total",
    "Metrics listener bind conflicts or failures.",
    registry=_INSTRUMENTATION_REGISTRY,
)

_KNOWN_PROVIDERS = frozenset(
    {
        "python-kma-api",
        "python-airkorea-api",
        "weatherapi",
        "openweathermap",
        "open_meteo",
        "visual_crossing",
        "tomorrow_io",
        "weatherbit",
        "weatherstack",
        "accuweather",
        "wttr_in",
    }
)
_KNOWN_DATASETS = frozenset(
    {
        "kma_weather_bundle",
        "kma_ultra_short_nowcast",
        "kma_ultra_short_forecast",
        "kma_short_forecast",
        "kma_mid_forecast",
        "kma_weather_alerts",
        "airkorea_station_catalog",
        "airkorea_measurement",
        "airkorea_realtime_measurement",
        "weatherapi_current",
        "weatherapi_forecast",
        "openweathermap_current",
        "openweathermap_forecast",
        "open_meteo_current",
        "open_meteo_forecast",
        "visual_crossing_timeline",
        "tomorrow_io_realtime",
        "tomorrow_io_forecast",
        "weatherbit_current",
        "weatherbit_forecast",
        "weatherstack_current",
        "accuweather_current",
        "accuweather_forecast",
        "wttr_in_current",
        "wttr_in_forecast",
    }
)
_SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_KNOWN_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_FIXED_ROUTES = frozenset(
    {
        "/health",
        "/version",
        "/metrics",
        "/v1/weather/locations",
        "/v1/weather/nearby",
        "/v1/weather/resolve",
        "/v1/weather/markers",
        "/v1/admin/locations",
        "/v1/admin/providers",
        "/v1/admin/provider-credentials",
        "/v1/admin/session-revocations/revoke",
        "/v1/admin/session-revocations/check",
        "/v1/admin/sync-runs",
    }
)
_state_lock = Lock()
_active_sync_counts: dict[tuple[str, str], int] = {}


def _label(value: object, known: frozenset[str]) -> str:
    """Return a bounded catalog label; never expose arbitrary identifiers."""
    text = str(value).strip().lower()
    return text if text in known and _SAFE_LABEL.fullmatch(text) else "other"


def provider_label(provider: object) -> str:
    return _label(provider, _KNOWN_PROVIDERS)


def dataset_label(dataset: object) -> str:
    return _label(dataset, _KNOWN_DATASETS)


def route_label(route: object) -> str:
    """Use a registered route template and collapse arbitrary paths."""
    text = str(route) if route else "unmatched"
    if len(text) > 160 or not text.startswith("/"):
        return "unmatched"
    # Starlette supplies templates (``{location_id}``) after routing.  A raw
    # path with no template is only safe for a small fixed system endpoint.
    if "{" not in text and text not in _FIXED_ROUTES:
        return "unmatched"
    return text


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"


def _safe(operation: str, callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        # Metrics must never turn a successful ingest/API response into a
        # failed request.  Keep a bounded diagnostic counter for operators.
        with suppress(Exception):
            METRIC_ERRORS.labels(operation=operation).inc()


def observe_http_request(
    *, method: str, route: object, status_code: int, duration_seconds: float
) -> None:
    candidate = str(method).upper()
    safe_method = candidate if candidate in _KNOWN_METHODS else "other"
    safe_route = route_label(route)
    def observe() -> None:
        HTTP_REQUESTS.labels(
            method=safe_method,
            route=safe_route,
            status_class=status_class(status_code),
        ).inc()
        HTTP_DURATION.labels(method=safe_method, route=safe_route).observe(
            max(0.0, duration_seconds)
        )

    _safe("http", observe)


def change_http_in_flight(method: str, delta: float) -> None:
    candidate = str(method).upper()
    safe_method = candidate if candidate in _KNOWN_METHODS else "other"
    _safe("http_in_flight", lambda: HTTP_IN_FLIGHT.labels(method=safe_method).inc(delta))


def observe_provider_request(
    provider: object, dataset: object, *, outcome: str, duration_seconds: float
) -> None:
    safe_provider = provider_label(provider)
    safe_dataset = dataset_label(dataset)
    safe_outcome = outcome if outcome in {"success", "error"} else "error"
    _safe(
        "provider",
        lambda: (
            PROVIDER_REQUESTS.labels(
                provider=safe_provider, dataset=safe_dataset, outcome=safe_outcome
            ).inc(),
            PROVIDER_DURATION.labels(provider=safe_provider, dataset=safe_dataset).observe(
                max(0.0, duration_seconds)
            ),
        ),
    )


def observe_sync_started(provider: object, dataset: object) -> None:
    safe_provider, safe_dataset = provider_label(provider), dataset_label(dataset)

    def update() -> None:
        SYNC_STARTED.labels(provider=safe_provider, dataset=safe_dataset).inc()
        key = (safe_provider, safe_dataset)
        with _state_lock:
            current = _active_sync_counts.get(key, 0) + 1
            _active_sync_counts[key] = current
        SYNC_ACTIVE.labels(provider=safe_provider, dataset=safe_dataset).set(current)

    _safe("sync_started", update)


def observe_sync_finished(
    provider: object,
    dataset: object,
    *,
    status: str,
    requests: int = 0,
    sources: int = 0,
    values: int = 0,
) -> None:
    safe_provider, safe_dataset = provider_label(provider), dataset_label(dataset)
    safe_status = status if status in {"success", "failed", "partial", "running"} else "other"
    request_count, source_count, value_count = (
        max(0, int(requests)),
        max(0, int(sources)),
        max(0, int(values)),
    )

    def update() -> None:
        SYNC_FINISHED.labels(
            provider=safe_provider, dataset=safe_dataset, status=safe_status
        ).inc()
        key = (safe_provider, safe_dataset)
        with _state_lock:
            current = max(0, _active_sync_counts.get(key, 0) - 1)
            _active_sync_counts[key] = current
        SYNC_ACTIVE.labels(provider=safe_provider, dataset=safe_dataset).set(current)
        if request_count:
            SYNC_REQUESTS.labels(provider=safe_provider, dataset=safe_dataset).inc(request_count)
        if source_count:
            SYNC_SOURCES.labels(provider=safe_provider, dataset=safe_dataset).inc(source_count)
        if value_count:
            SYNC_VALUES.labels(provider=safe_provider, dataset=safe_dataset).inc(value_count)

    _safe("sync_finished", update)


def observe_stale_recovered(count: int) -> None:
    if count > 0:
        _safe("sync_stale", lambda: SYNC_STALE_RECOVERED.inc(max(0, count)))


def metrics_payload() -> bytes:
    """Serialize the isolated registry for an HTTP scrape."""
    cleanup_dead_multiprocess_gauges()
    return generate_latest(REGISTRY)


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


_server_lock = Lock()
_server_ports: set[int] = set()


def start_metrics_server(port: int, *, address: str = "0.0.0.0") -> bool:
    """Start a worker metrics listener once and expose bind failures in metrics."""
    if not 1024 <= port <= 65535:
        raise ValueError("metrics port는 1024~65535 범위여야 합니다.")
    with _server_lock:
        if port in _server_ports:
            return True
        try:
            start_http_server(port, addr=address, registry=REGISTRY)
        except OSError:
            _safe("metrics_server_bind", lambda: METRICS_SERVER_BIND_FAILURES.inc())
            _safe("metrics_server_state", lambda: METRICS_SERVER_UP.set(0))
            return False
        _server_ports.add(port)
        _safe("metrics_server_state", lambda: METRICS_SERVER_UP.set(1))
        return True


@contextmanager
def provider_request(provider: object, dataset: object) -> Iterator[None]:
    """Observe one logical provider call without changing its exception path."""
    started = perf_counter()
    try:
        yield
    except Exception:
        observe_provider_request(
            provider, dataset, outcome="error", duration_seconds=perf_counter() - started
        )
        raise
    else:
        observe_provider_request(
            provider, dataset, outcome="success", duration_seconds=perf_counter() - started
        )


__all__ = [
    "REGISTRY",
    "change_http_in_flight",
    "metrics_content_type",
    "metrics_payload",
    "observe_http_request",
    "observe_provider_request",
    "observe_stale_recovered",
    "observe_sync_finished",
    "observe_sync_started",
    "provider_request",
    "start_metrics_server",
]
