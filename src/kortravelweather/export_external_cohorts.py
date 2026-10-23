"""HTTP 전에 선언하는 실제 외부 수집 선택 집합. cap/sampling을 숨기지 않는다."""

import json
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import ExportArtifactError
from .export_external import RetrievalScopeDescriptor
from .export_heads import ExportHeads

MAX_EXTERNAL_TARGETS = 5000
MAX_COHORT_BYTES = 2 * 1024 * 1024


def external_cohort_table(metadata: MetaData):
    name = "weather_export_external_cohorts"
    if name in metadata.tables:
        return metadata.tables[name]
    return Table(
        name,
        metadata,
        Column("provider", String(40), primary_key=True),
        Column("dataset_key", String(120), primary_key=True),
        Column("run_id", String(36), nullable=False),
        Column("run_started_at", DateTime(timezone=True), nullable=False),
        Column("targets", JSON, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )


def declare_external_cohort(repository, *, run_id, provider, dataset_key, targets):
    """더 최근 시작 run만 roster를 갱신한다. API는 provider cartesian을 추측하지 않는다."""
    if not 1 <= len(targets) <= MAX_EXTERNAL_TARGETS:
        raise ExportArtifactError("외부 수집 선택 집합 상한이 올바르지 않습니다.")
    selected = []
    for target in sorted(targets, key=lambda t: t.location_id):
        descriptor = RetrievalScopeDescriptor(
            provider=provider,
            dataset_key=dataset_key,
            forecast_style="short" if dataset_key.endswith("forecast") else "observed",
            entity_id=target.location_id,
            location_ids=(target.location_id,),
            latitude=target.latitude,
            longitude=target.longitude,
            normalizer_revision=f"{provider}-v1",
        )
        selected.append(
            {
                "location_id": descriptor.entity_id,
                "latitude": descriptor.latitude,
                "longitude": descriptor.longitude,
            }
        )
    if len({t["location_id"] for t in selected}) != len(selected):
        raise ExportArtifactError("외부 선택 집합 위치가 중복됩니다.")
    if len(json.dumps(selected, ensure_ascii=False).encode()) > MAX_COHORT_BYTES:
        raise ExportArtifactError("외부 선택 집합 metadata byte 상한을 넘었습니다.")
    heads = ExportHeads(repository)
    table = external_cohort_table(heads.metadata)
    runs = heads.metadata.tables["weather_sync_runs"]
    with heads._transaction() as s:
        run = s.execute(
            select(runs.c.started_at, runs.c.status, runs.c.provider, runs.c.dataset_key).where(
                runs.c.run_id == run_id
            )
        ).one()
        if run.status != "running" or (run.provider, run.dataset_key) != (provider, dataset_key):
            raise ExportArtifactError("진행 중인 동일 원천 run만 roster를 선언할 수 있습니다.")
        statement = insert(table).values(
            provider=provider,
            dataset_key=dataset_key,
            run_id=run_id,
            run_started_at=run.started_at,
            targets=selected,
            updated_at=datetime.now(UTC),
        )
        result = s.execute(
            statement.on_conflict_do_update(
                index_elements=["provider", "dataset_key"],
                set_={
                    k: getattr(statement.excluded, k)
                    for k in ["run_id", "run_started_at", "targets", "updated_at"]
                },
                where=tuple_(table.c.run_started_at, table.c.run_id)
                < tuple_(run.started_at, run_id),
            )
        )
        if result.rowcount != 1:
            current = s.execute(
                select(table.c.run_id, table.c.targets).where(
                    table.c.provider == provider, table.c.dataset_key == dataset_key
                )
            ).one()
            if current.run_id != run_id or current.targets != selected:
                raise ExportArtifactError(
                    "최신 roster 소유권이 바뀌었거나 동일 run 선언이 다릅니다."
                )
    return selected
