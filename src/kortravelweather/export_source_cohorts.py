"""실제 요청할 SDK/KMA source 집합을 HTTP 전에 작은 roster로 선언한다."""

import json
from datetime import UTC, datetime

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert

from .export_artifacts import ExportArtifactError
from .export_external_cohorts import MAX_COHORT_BYTES, MAX_EXTERNAL_TARGETS, external_cohort_table
from .export_heads import ExportHeads, ScopeDescriptor


def declare_source_cohort(repository, *, run_id, provider, dataset_key, descriptors):
    """사전 source plan과 실제 canonical target을 보존한다. caller가 오류를 격리한다."""
    if not 1 <= len(descriptors) <= MAX_EXTERNAL_TARGETS:
        raise ExportArtifactError("선언 source 개수가 상한을 벗어났습니다.")
    scopes, ids = [], set()
    for descriptor in descriptors:
        if descriptor.provider != provider or descriptor.dataset_key != dataset_key:
            raise ExportArtifactError("사전 source 집합의 provider/dataset이 다릅니다.")
        if ids.intersection(descriptor.location_ids):
            raise ExportArtifactError("동일 dataset 위치가 사전 source 집합에 중복됩니다.")
        ids.update(descriptor.location_ids)
        scopes.append(descriptor.model_dump(mode="json"))
    if len(ids) > MAX_EXTERNAL_TARGETS or len(json.dumps(scopes).encode()) > MAX_COHORT_BYTES:
        raise ExportArtifactError("사전 source 집합의 위치/metadata byte 상한을 넘었습니다.")
    heads = ExportHeads(repository)
    table, runs = external_cohort_table(heads.metadata), heads.metadata.tables["weather_sync_runs"]
    catalog = heads.metadata.tables["weather_locations"]
    with heads._transaction() as s:
        run = s.execute(select(
            runs.c.started_at, runs.c.status, runs.c.provider, runs.c.dataset_key,
            runs.c.export_cohort_required,
        ).where(runs.c.run_id == run_id)).one()
        if (
            run.status != "running" or not run.export_cohort_required
            or (run.provider, run.dataset_key) != (provider, dataset_key)
        ):
            raise ExportArtifactError("원자적 native intent가 있는 동일 수집 실행이 필요합니다.")
        locations = {row["location_id"]: row for row in s.execute(select(
            catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
            catalog.c.nx, catalog.c.ny,
        ).where(catalog.c.location_id.in_(ids), catalog.c.enabled.is_(True))).mappings()}
        if set(locations) != ids:
            raise ExportArtifactError("선택 source의 활성 canonical 위치가 없습니다.")
        for descriptor in descriptors:
            for key in descriptor.location_ids:
                row = locations[key]
                if isinstance(descriptor, ScopeDescriptor):
                    valid = (row["nx"], row["ny"]) == (descriptor.nx, descriptor.ny)
                else:
                    valid = (float(row["latitude"]), float(row["longitude"])) == (
                        descriptor.latitude, descriptor.longitude,
                    )
                if not valid:
                    raise ExportArtifactError("선택 source와 canonical 좌표/grid가 다릅니다.")
        targets = [{
            "location_id": key, "latitude": float(locations[key]["latitude"]),
            "longitude": float(locations[key]["longitude"]),
        } for key in sorted(ids)]
        statement = insert(table).values(
            provider=provider, dataset_key=dataset_key, run_id=run_id,
            run_started_at=run.started_at, targets=targets, descriptors=scopes,
            updated_at=datetime.now(UTC),
        )
        result = s.execute(statement.on_conflict_do_update(
            index_elements=["provider", "dataset_key"],
            set_={key: getattr(statement.excluded, key) for key in (
                "run_id", "run_started_at", "targets", "descriptors", "updated_at",
            )},
            where=tuple_(table.c.run_started_at, table.c.run_id) < tuple_(run.started_at, run_id),
        ))
        if result.rowcount != 1:
            current = s.execute(select(table.c.run_id, table.c.descriptors).where(
                table.c.provider == provider, table.c.dataset_key == dataset_key,
            )).one()
            if current.run_id != run_id or current.descriptors != scopes:
                raise ExportArtifactError("최신 source roster 소유권이 바뀌었습니다.")
    return targets
