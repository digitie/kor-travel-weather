"""복구 sensor의 소비자별 선택 등록. 운영 활성화는 별도 검증 후 수행한다."""

import os
from collections.abc import Mapping

from dagster import JobDefinition, SensorDefinition
from kortravelcommon.dagster import RecoveryPolicy, infrastructure_retry_sensor_for_jobs
from kortravelcommon.dagster_recovery import native_incarnation_recovery_sensor

INFRA_RETRY_ENABLED_ENV = "KOR_TRAVEL_WEATHER_INFRA_RETRY_ENABLED"
NATIVE_ORIGIN_ENV = "KOR_TRAVEL_WEATHER_NATIVE_RECOVERY_REPOSITORY_ORIGIN_ID"
LOCATION_NAME = "kortravelweather_dagster.definitions"


def optional_recovery_sensors(
    jobs: Mapping[str, tuple[JobDefinition, RecoveryPolicy]],
) -> list[SensorDefinition]:
    """기존 앱 DB 정산과 구분하여 인프라 재시도·native 정산을 명시적으로 등록한다."""
    enabled = os.environ.get(INFRA_RETRY_ENABLED_ENV, "false")
    if enabled not in {"true", "false"}:
        raise ValueError(f"{INFRA_RETRY_ENABLED_ENV}는 true 또는 false여야 합니다.")
    sensors = []
    if enabled == "true":
        sensors.append(
            infrastructure_retry_sensor_for_jobs(
                name="weather_infrastructure_retry",
                project="weather",
                location_name=LOCATION_NAME,
                jobs=jobs,
            )
        )
    origin_id = os.environ.get(NATIVE_ORIGIN_ENV)
    if origin_id:
        sensors.append(
            native_incarnation_recovery_sensor(
                name="weather_native_incarnation_recovery",
                project="weather",
                location_name=LOCATION_NAME,
                repository_origin_id=origin_id,
            )
        )
    return sensors
