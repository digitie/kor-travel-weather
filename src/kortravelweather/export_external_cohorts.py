"""HTTP 전에 선언하는 실제 외부 수집 선택 집합. cap/sampling을 숨기지 않는다."""

import json
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import ExportArtifactError
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
    from .export_external import RetrievalScopeDescriptor
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


def selected_external_descriptors(connection, metadata, locations, providers, *, forecast=False):
    """호출자는 같은 MVCC snapshot에서 catalog/head와 이 선택 집합을 함께 읽는다."""
    from .export_external import RetrievalScopeDescriptor
    table = external_cohort_table(metadata)
    runs = metadata.tables["weather_sync_runs"]
    suffix = "forecast" if forecast else "current"
    expected = {(provider, f"{provider}_{suffix}") for provider in providers}
    cohorts = list(
        connection.execute(
            select(table, runs.c.status.label("native_status"))
            .outerjoin(runs, runs.c.run_id == table.c.run_id)
            .where(tuple_(table.c.provider, table.c.dataset_key).in_(expected))
        ).mappings()
    )
    if {(r["provider"], r["dataset_key"]) for r in cohorts} != expected:
        raise ExportArtifactError("요청 provider의 실제 수집 선택 집합이 준비되지 않았습니다.")
    catalog = {r["location_id"]: r for r in locations}
    result = []
    expected_runs = {}
    for row in cohorts:
        # native INSERT와 원자적으로 저장된 intent를 작은 부분 index에서 한 행만
        # 읽는다. roster 자체의 잠금/쓰기 실패도 이전 성공으로 숨기지 않는다.
        latest = connection.scalar(
            select(runs.c.run_id).where(
                runs.c.provider == row["provider"], runs.c.dataset_key == row["dataset_key"],
                runs.c.export_cohort_required.is_(True),
            ).order_by(runs.c.started_at.desc(), runs.c.run_id.desc()).limit(1)
        )
        if latest != row["run_id"]:
            raise ExportArtifactError("최신 native 수집 선택 선언이 확인되지 않았습니다.")
        # claim 이전 prepare 실패는 source owner에 흔적을 남기지 않는다. HTTP 전
        # roster의 native 실행도 확인해야 이전 성공 owner가 실패를 가리지 못한다.
        if row["native_status"] != "success":
            raise ExportArtifactError("최신 수집 선택 집합의 native 성공이 확인되지 않았습니다.")
        targets = row["targets"]
        if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_EXTERNAL_TARGETS:
            raise ExportArtifactError("수집 선택 집합 상한이 올바르지 않습니다.")
        seen = set()
        for target in targets:
            key = target["location_id"]
            item = catalog.get(key)
            if (
                key in seen
                or item is None
                or any(
                    float(item[field]) != float(target[field])
                    for field in ["latitude", "longitude"]
                )
            ):
                raise ExportArtifactError("수집 선택 집합과 현재 위치 catalog가 다릅니다.")
            seen.add(key)
            descriptor = RetrievalScopeDescriptor(
                provider=row["provider"],
                dataset_key=row["dataset_key"],
                forecast_style="short" if forecast else "observed",
                entity_id=key,
                location_ids=(key,),
                latitude=target["latitude"],
                longitude=target["longitude"],
                normalizer_revision=f"{row['provider']}-v1",
            )
            result.append(descriptor)
            expected_runs[descriptor.namespace] = row["run_id"]
    heads = metadata.tables["weather_export_heads"]
    generations = metadata.tables["weather_export_generations"]
    attempts = metadata.tables["weather_export_attempts"]
    approved = dict(
        connection.execute(
            select(heads.c.namespace, attempts.c.run_id)
            .select_from(
                heads.join(generations, heads.c.generation_id == generations.c.generation_id)
                .join(attempts, generations.c.attempt_id == attempts.c.attempt_id)
            )
            .where(heads.c.namespace.in_(expected_runs))
        ).all()
    )
    if approved != expected_runs:
        raise ExportArtifactError("최신 선택 집합 전체의 승인 head가 확인되지 않았습니다.")
    return sorted(result, key=lambda d: (d.provider, d.entity_id))
