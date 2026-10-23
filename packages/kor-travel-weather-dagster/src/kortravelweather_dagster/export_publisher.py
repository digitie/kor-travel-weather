"""worker를 띄우지 않는 작은 export sweep. 등록만으로 활성화하지 않는다."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from dagster import DefaultSensorStatus, SensorDefinition, SkipReason, sensor
from sqlalchemy import text

from kortravelweather.export_collector import (
    dagster_status_lookup,
    publish_pending_exports,
    refresh_publisher_daily,
)
from kortravelweather.repository import WeatherRepository
from kortravelweather.settings import WeatherSettings

_TICK_SLOT = threading.BoundedSemaphore(1)
_LEASE_KEY = "kortravelweather:export-publisher:v1"
MAX_TICK_ATTEMPTS = 256


def _cursor(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 256:
        raise ValueError("publisher cursor가 올바르지 않습니다.")
    decoded = json.loads(value)
    if not isinstance(decoded, dict) or set(decoded) != {"v", "after"} or decoded["v"] != 1:
        raise ValueError("publisher cursor가 올바르지 않습니다.")
    after = decoded["after"]
    if after is not None and (not isinstance(after, str) or str(UUID(after)) != after):
        raise ValueError("publisher cursor가 올바르지 않습니다.")
    return after


@contextmanager
def _publisher_lease(repository: WeatherRepository) -> Iterator[bool]:
    """전용 connection 하나만 lease로 유지한다. business transaction은 보유하지 않는다.

    수동 publisher는 기존 CAS/file guard로 보호된다. PostgreSQL 이외 DB에서는
    자동 publisher를 실행하지 않으며 lease 연결 소실도 다음 tick에서 재시도한다.
    """
    if repository.engine.dialect.name != "postgresql":
        yield False
        return
    with repository.engine.connect() as connection:
        acquired = False
        try:
            with connection.begin():
                connection.execute(text("SET LOCAL statement_timeout = '2s'"))
                acquired = bool(connection.scalar(text(
                    "SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"
                ), {"key": _LEASE_KEY}))
            yield acquired
        finally:
            if acquired:
                try:
                    with connection.begin():
                        connection.execute(text("SET LOCAL statement_timeout = '2s'"))
                        unlocked = connection.scalar(text(
                            "SELECT pg_advisory_unlock(hashtextextended(:key, 0))"
                        ), {"key": _LEASE_KEY})
                        if unlocked is not True:
                            connection.invalidate()
                except Exception:
                    # session lock가 pool에 남지 않도록 물리 연결까지 폐기한다.
                    connection.invalidate()
                    raise


def _repository(context, settings):
    resource = context.resources.weather_repository
    return WeatherRepository(
        resource.database_url or settings.database_url,
        export_status_enabled=settings.service_exports_enabled,
    )


def build_export_publisher_sensor(
    *,
    settings_factory: Callable = WeatherSettings,
    repository_factory: Callable = _repository,
) -> SensorDefinition:
    """factory는 새 repository 소유권을 넘긴다. 모든 경로에서 dispose한다.

    12초는 후보 사이 협력 예산이다. 파일검증/DB연결/마지막 SQL의 최종 대기를
    포함한 전체 wall-time 보장은 아니다. SQL은 기존 5초/잠금 상한을 유지한다.
    """

    @sensor(
        name="weather_export_publisher",
        minimum_interval_seconds=180,
        default_status=DefaultSensorStatus.STOPPED,
        required_resource_keys={"weather_repository"},
    )
    def publisher(context):
        repository = None
        acquired = False
        try:
            settings = settings_factory()
            root = settings.service_export_artifact_root
            if not settings.service_exports_enabled or root is None:
                return SkipReason("export publisher는 명시 활성화와 artifact root가 필요합니다.")
            if not root.is_absolute():
                return SkipReason("export artifact root는 절대 경로여야 합니다.")
            acquired = _TICK_SLOT.acquire(blocking=False)
            if not acquired:
                return SkipReason("이 프로세스에서 export publisher를 평가 중입니다.")
            try:
                after = _cursor(context.cursor)
            except (ValueError, TypeError):
                context.log.warning("weather_export_publisher_cursor_reset")
                after = None
            repository = repository_factory(context, settings)
            with _publisher_lease(repository) as leased:
                if not leased:
                    return SkipReason("다른 publisher가 실행 중이거나 lease가 불명확합니다.")
                deadline, processed = time.monotonic() + 12, 0
                lookup = dagster_status_lookup(context.instance)
                # RAM 상한은 기존 2개 microbatch다. 총량/시간은 별도 제한한다.
                for _ in range(MAX_TICK_ATTEMPTS // 2):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    progress = publish_pending_exports(
                        repository, root, cursor=after, limit=2, budget_seconds=remaining,
                        dagster_status=lookup,
                    )
                    processed += progress.processed
                    after = progress.next_cursor
                    if not progress.has_more or progress.processed == 0:
                        break
                refresh_publisher_daily(repository, root, deadline=deadline)
            context.update_cursor(json.dumps(
                {"v": 1, "after": after}, separators=(",", ":"),
            ))
            return SkipReason(f"export publisher: {processed}개 후보 확인")
        except Exception:
            # DB URL/provider payload/token이 포함된 exception 원문을 남기지 않는다.
            context.log.warning("weather_export_publisher_deferred")
            return SkipReason("export publisher 평가를 다음 tick에 재시도합니다.")
        finally:
            if repository is not None:
                try:
                    repository.engine.dispose()
                except Exception:
                    context.log.warning("weather_export_publisher_dispose_deferred")
            if acquired:
                _TICK_SLOT.release()

    return publisher


weather_export_publisher_sensor = build_export_publisher_sensor()
