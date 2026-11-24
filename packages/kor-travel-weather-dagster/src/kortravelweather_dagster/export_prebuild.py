"""Map 표준 프로필 export 페이지를 publisher 뒤 별도 유한 job으로 미리 만든다(호출 검토 A3).

publisher sensor(60초 tick)에 넣지 않는다. 사전 구성 한 번은 freeze 한 번 + 전체 페이지 조립이라
odroid에서 수십 초가 걸려 sensor tick 상한(40~45초)에 들어가지 않는다. 승격이 일어날 때마다 run을
만들지도 않는다(하루 수천 run·run worker 기동 비용, #289 판단과 같다).

대신 Map W2 소비 시각 직전에만 돈다. Map ``weather_state_current``는 매시 :25·:40(KST)이고
(map ``weather_state.py``), 이 job은 그 2분 전인 :23·:38에 돈다. 사이에 current 원천 승격이나
catalog 변경이 있으면 proof가 달라져 요청은 기존 freeze 경로로 간다(정확성은 그대로, 속도만 잃는다).
입력이 같으면 builder는 아무 것도 쓰지 않는다(no-op).

실행 조건은 #289의 정리 job과 같다(``export_maintenance.publisher_sensor_running``): export와
사전 구성이 켜져 있고 이 location의 publisher sensor가 RUNNING일 때만 run을 만든다(freeze로
publisher를 멈추면 사전 구성도 멈춘다).
"""

from __future__ import annotations

from typing import Any

from dagster import (
    DefaultScheduleStatus,
    Failure,
    in_process_executor,
    job,
    op,
)
from kortravelcommon.dagster import RecoveryPolicy, coalescing_schedule

from kortravelweather.export_prebuilt import (
    SUCCESS_OUTCOMES,
    build_current_profile,
    prebuild_enabled,
)
from kortravelweather.settings import WeatherSettings

JOB_NAME = "weather_export_prebuild_job"
SCHEDULE_NAME = "weather_export_prebuild"
LOCATION_NAME = "kortravelweather_dagster.definitions"
#: Map W2(:25·:40) 2분 전. 매시 KMA(:00·:05·:20)·AirKorea(:35)와 같은 분에 시작하지 않는다.
CRON_SCHEDULE = "23,38 * * * *"
#: builder 예산(600초)에 마지막 저장 문장(30초)과 여유를 더한 run 상한.
MAX_RUNTIME_SECONDS = 900
#: 정상 결과. 배포 hook CLI와 같은 집합이다(``export_prebuilt.SUCCESS_OUTCOMES`` 설명 참고).
_SUCCESS = SUCCESS_OUTCOMES

_POLICY = RecoveryPolicy(MAX_RUNTIME_SECONDS, idempotent=True, infrastructure_retries=1)


def prebuild_policy() -> RecoveryPolicy:
    return _POLICY


@op(required_resource_keys={"weather_repository"})
def weather_export_prebuild(context) -> dict[str, Any]:
    from kortravelweather.export_collector import failure_reason

    settings = WeatherSettings()
    if not prebuild_enabled(settings):
        context.log.info("export 또는 사전 구성이 꺼져 있어 만들 것이 없습니다.")
        return {"outcome": "disabled"}
    repository = context.resources.weather_repository.create_repository()
    try:
        report = build_current_profile(repository, settings)
    except Exception as exc:
        # DB URL·SQL 파라미터가 담길 수 있는 원문 대신 분류만 남긴다.
        raise Failure(description=f"export 사전 구성 실패({failure_reason(exc)})") from None
    finally:
        repository.engine.dispose()
    context.log.info(
        "weather_export_prebuild "
        + " ".join(f"{key}={value}" for key, value in sorted(report.items()))
    )
    metadata = {key: str(value) for key, value in report.items()}
    if report["outcome"] not in _SUCCESS:
        raise Failure(description=f"export 사전 구성 미완료({report['outcome']})",
                      metadata=metadata)
    context.add_output_metadata(metadata)
    return report


@job(
    name=JOB_NAME,
    executor_def=in_process_executor,
    tags=_POLICY.tags(project="weather", job_name=JOB_NAME),
    description="Map 표준 프로필(current limit 2000) export 페이지를 publisher 뒤 미리 만든다.",
)
def weather_export_prebuild_job():
    weather_export_prebuild()


def _should_execute(context) -> bool:
    # 정리 job(#289)과 같은 판정을 쓴다: publisher를 멈추면 사전 구성도 멈춘다.
    from .export_maintenance import publisher_sensor_running

    return prebuild_enabled(WeatherSettings()) and publisher_sensor_running(context.instance)


weather_export_prebuild_schedule = coalescing_schedule(
    project="weather",
    location_name=LOCATION_NAME,
    job=weather_export_prebuild_job,
    should_execute=_should_execute,
    name=SCHEDULE_NAME,
    cron_schedule=CRON_SCHEDULE,
    execution_timezone="Asia/Seoul",
    # 기본 RUNNING이지만 publisher sensor가 RUNNING이고 export가 켜진 곳에서만 run을 만든다.
    default_status=DefaultScheduleStatus.RUNNING,
)
