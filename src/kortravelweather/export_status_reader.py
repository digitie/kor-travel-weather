"""작은 상태표만 읽는 W4 응답. 사실 이력·workspace·원천 API는 조회하지 않는다."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, true

from .export_artifacts import ExportArtifactError
from .export_status import ExportSourceStatuses, status_transaction
from .export_status_scopes import status_scopes


def read_status(repository, providers: set[str], *, as_of: datetime) -> tuple[dict, str]:
    keys = status_scopes(providers)
    if not keys or providers != {key.provider for key in keys}:
        raise ExportArtifactError("상태 scope가 설정되지 않았습니다.")
    now = as_of.astimezone(UTC)
    repository.reconcile_export_status(providers, limit=1)
    from .repository import SyncRunRow

    with repository.engine.connect().execution_options(
        isolation_level="REPEATABLE READ",
    ) as connection, connection.begin(), status_transaction(connection):
        if connection.scalar(select(SyncRunRow.run_id).where(
            SyncRunRow.export_status_pending == true(), SyncRunRow.provider.in_(providers),
        ).limit(1)) is not None:
            raise ExportArtifactError("수집 상태 재투영을 기다리고 있습니다.")
        states = ExportSourceStatuses(repository_metadata()).read(connection, keys, as_of=now)
    # 계획/스케줄만으로 성공처럼 보이지 않도록 최초 terminal까지 준비 중으로 둔다.
    if not any(state.last_status is not None for state in states):
        raise ExportArtifactError("수집 terminal 상태가 아직 없습니다.")
    items = [{
        "provider": state.key.provider,
        "dataset_key": state.key.dataset_key,
        "forecast_style": state.key.forecast_style,
        "last_status": state.last_status,
        "last_success_at": state.last_success_at,
        "last_failed_at": state.last_failed_at,
        "last_attempt_failed": state.last_attempt_failed,
        "next_scheduled_at": state.next_scheduled_at,
    } for state in states]
    clocks = [state.source_collected_at for state in states]
    successful = any(state.last_success_at is not None for state in states)
    stale = any(
        state.last_status != "success"
        or state.source_collected_at is None
        or state.source_collected_at > now
        or now - state.source_collected_at > _max_age(state.key.dataset_key)
        for state in states
    )
    meta = {
        "generated_at": now,
        "source_collected_at": min(clocks) if all(clock is not None for clock in clocks) else None,
        "source_status": "failed" if not successful else "stale" if stale else "ok",
        "item_count": len(items),
    }
    # generated_at은 매 요청 생성된다. 의미가 같은 표현에 대해 weak validator를 쓴다.
    digest = hashlib.sha256(json.dumps(
        {"items": items, "meta": {k: v for k, v in meta.items() if k != "generated_at"}},
        sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat(),
    ).encode()).hexdigest()
    return {"items": items, "meta": meta}, f'W/"{digest}"'


def repository_metadata():
    # repository는 scope selector를 import하므로 실행 시점에만 연결한다.
    from .repository import Base

    return Base.metadata


def _max_age(dataset: str) -> timedelta:
    if dataset in {"khoa_beach_index", "krforest_mountain_weather", "krforest_dust",
                   "krex_restarea_weather"}:
        return timedelta(hours=36)
    if dataset == "kma_mid_forecast":
        return timedelta(hours=18)
    if dataset == "kma_short_forecast":
        return timedelta(hours=6)
    if dataset in {"kma_ultra_short_nowcast", "kma_ultra_short_forecast"}:
        return timedelta(hours=3)
    return timedelta(hours=6)
