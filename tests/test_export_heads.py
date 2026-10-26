from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore
from kortravelweather.export_heads import ExportHeads, ScopeDescriptor
from kortravelweather.export_receipts import ExportCapture
from kortravelweather.models import WeatherLocation
from kortravelweather.repository import WeatherRepository


@pytest.mark.parametrize(
    # 23시 단기예보는 D+4 0시까지 1시간 간격이다(첫 정시 D+1 0시부터 73개).
    "style,base_time,hours", [("ultra_short", "2330", 6), ("short", "2300", 73)]
)
def test_forecast_coverage_uses_sdk_base_across_kst_midnight(style, base_time, hours):
    from kma.time_utils import parse_kma_datetime

    from kortravelweather.export_heads import _forecast_coverage

    base = parse_kma_datetime("20261023", base_time)
    descriptor = ScopeDescriptor(
        dataset_key="kma_ultra_short_forecast" if style == "ultra_short" else "kma_short_forecast",
        forecast_style=style,
        entity_type="weather_response",
        entity_id="grid:60:127",
        nx=60,
        ny=127,
        location_ids=("midnight",),
        normalizer_revision="kma-v1",
        winner_policy_revision="kma-base-v1",
        coverage_contract="kma-grid-forecast-v1",
    )
    categories = (
        {"T1H", "RN1", "REH", "PTY", "SKY", "VEC", "WSD"}
        if style == "ultra_short"
        else {"TMP", "PCP", "REH", "PTY", "POP", "SKY", "VEC", "WSD"}
    )
    rows = []
    for hour in range(1, hours + 1):
        at = (base + timedelta(hours=hour)).replace(minute=0)
        rows.extend(
            {
                "fcstDate": at.strftime("%Y%m%d"),
                "fcstTime": at.strftime("%H%M"),
                "category": category,
            }
            for category in categories
        )
    assert rows[0]["fcstDate"] == "20261024" and rows[0]["fcstTime"] == "0000"
    _forecast_coverage(descriptor, rows, base.astimezone(UTC))
    with pytest.raises(ExportArtifactError):
        _forecast_coverage(descriptor, rows[len(categories) :], base.astimezone(UTC))


_SHORT_CATEGORIES = ("TMP", "PCP", "REH", "PTY", "POP", "SKY", "VEC", "WSD")


def _short_rows(base_date, base_time, hourly, *, tail=8, drop=()):
    """운영 단기예보 응답 모양: 1시간 간격 ``hourly``개 정시 뒤 3시간 간격 ``tail``개."""
    from kma.time_utils import parse_kma_datetime

    base = parse_kma_datetime(base_date, base_time)
    first = base.replace(minute=0) + timedelta(hours=1)
    times = [first + timedelta(hours=i) for i in range(hourly)]
    times += [times[-1] + timedelta(hours=3 * (i + 1)) for i in range(tail)]
    rows = [
        {"fcstDate": at.strftime("%Y%m%d"), "fcstTime": at.strftime("%H%M"), "category": c}
        for index, at in enumerate(times)
        if index not in drop
        for c in _SHORT_CATEGORIES
    ]
    return rows, base.astimezone(UTC)


def _short_descriptor():
    return ScopeDescriptor(
        dataset_key="kma_short_forecast",
        forecast_style="short",
        entity_type="weather_response",
        entity_id="grid:57:103",
        nx=57,
        ny=103,
        location_ids=("short",),
        normalizer_revision="kma-v1",
        winner_policy_revision="kma-base-v1",
        coverage_contract="kma-grid-forecast-v1",
    )


@pytest.mark.parametrize(
    "base_date,base_time,hourly",
    # 운영 실측: 05시 67개, 08시 64개, 20시 76개 정시가 1시간 간격이고 뒤는 3시간 간격 8개.
    [("20261027", "0500", 67), ("20261026", "0800", 64), ("20261026", "2000", 76)],
)
def test_short_forecast_requires_the_hourly_horizon_its_base_publishes(
    base_date, base_time, hourly
):
    from kortravelweather.export_heads import _forecast_coverage

    rows, revision = _short_rows(base_date, base_time, hourly)
    _forecast_coverage(_short_descriptor(), rows, revision)
    # 1시간 간격 구간의 마지막 정시(D+3 또는 D+4 0시)가 빠지면 연속 구간이 끊긴다.
    rows, revision = _short_rows(base_date, base_time, hourly, drop={hourly - 1})
    with pytest.raises(ExportArtifactError):
        _forecast_coverage(_short_descriptor(), rows, revision)


def test_late_short_base_cannot_pass_with_an_early_base_horizon():
    """20시 발표가 05시 발표처럼 D+3 0시에서 1시간 간격을 멈추면 승인하지 않는다."""
    from kortravelweather.export_heads import _forecast_coverage

    rows, revision = _short_rows("20261026", "2000", 52)
    with pytest.raises(ExportArtifactError):
        _forecast_coverage(_short_descriptor(), rows, revision)


@pytest.fixture(scope="module")
def repository():
    result = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    result.create_schema()
    yield result
    result.engine.dispose()


def _descriptor(repository):
    key = "heads-" + uuid.uuid4().hex
    repository.create_location(
        WeatherLocation(
            location_id=key,
            name="검증",
            latitude=37.5,
            longitude=127,
            nx=60,
            ny=127,
        )
    )
    return ScopeDescriptor(
        entity_type="weather_response",
        entity_id=key,
        nx=60,
        ny=127,
        location_ids=(key,),
        normalizer_revision="kma-v1",
        winner_policy_revision="kma-base-v1",
    )


def _candidate(
    repository,
    tmp_path,
    descriptor,
    *,
    revision=None,
    value="1",
    finish="success",
    record_mutator=None,
    values_mutator=None,
    capture_clock_mutator=None,
    post_bind_record_mutator=None,
    cohort=False,
):
    heads = ExportHeads(repository)
    revision = revision or datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    run = repository.start_sync_run(
        provider=descriptor.provider, dataset_key=descriptor.dataset_key,
        **({"export_cohort_required": True} if cohort else {}),
    )
    if cohort:
        from kortravelweather.export_source_cohorts import declare_source_cohort

        declare_source_cohort(repository, run_id=run.run_id, provider=descriptor.provider,
                              dataset_key=descriptor.dataset_key, descriptors=[descriptor])
    identity = repository.begin_export_attempt(
        run_id=run.run_id,
        scope_key="test",
        planned_sources=[descriptor.source_scope],
        execution_mode="native",
    )
    heads.lifecycle.claim(identity)
    heads.declare(identity, descriptor)
    kst = revision.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Seoul"))
    source_key = "candidate-" + uuid.uuid4().hex
    row = {
        "category": "T1H",
        "obsrValue": value,
        "nx": 60,
        "ny": 127,
        "baseDate": kst.strftime("%Y%m%d"),
        "baseTime": kst.strftime("%H%M"),
    }
    record = dict(
        source_record_key=source_key,
        provider=descriptor.provider,
        dataset_key=descriptor.dataset_key,
        source_entity_type=descriptor.entity_type,
        source_entity_id=descriptor.entity_id,
        run_id=run.run_id,
        fetched_at=datetime.now(UTC),
        payload={
            "rows": [row],
            "response_metadata": {
                "base_date": row["baseDate"],
                "base_time": row["baseTime"],
                "request_params": {"nx": 60, "ny": 127},
                "pagination": {
                    "expected_total_count": 1,
                    "received_item_count": 1,
                    "page_count": 1,
                    "complete": True,
                },
            },
        },
    )
    if record_mutator:
        record_mutator(record)
    from kortravelweather.providers.kma import ultra_short_nowcast_to_weather_values

    values = [
        v
        for key in descriptor.location_ids
        for v in ultra_short_nowcast_to_weather_values(
            record["payload"]["rows"],
            location_id=key,
            source_record_key=source_key,
            known_at=record["fetched_at"],
        )
    ]
    if values_mutator:
        values = values_mutator(values)
    heads.bind_kma_response(identity, record)
    if post_bind_record_mutator:
        post_bind_record_mutator(record)
    heads.lifecycle.expect_chunk(identity, "one", records=[record], values=values)
    capture = ExportCapture(
        identity,
        "one",
        {
            source_key: capture_clock_mutator(record["fetched_at"])
            if capture_clock_mutator
            else record["fetched_at"]
        },
        ExportArtifactStore(tmp_path),
    )
    loaded = repository.ingest_batch(source_records=[record], values=values, export_capture=capture)
    heads.lifecycle.close_source(identity, descriptor.source_scope, ["one"])
    heads.lifecycle.close_plan(identity)
    if finish:
        repository.finish_sync_run(run.run_id, status=finish)
    return heads, identity, capture, record, values, loaded


def _head(heads, descriptor):
    with heads.repository.engine.connect() as connection:
        row = (
            connection.execute(
                select(heads.heads).where(
                    heads.heads.c.namespace == descriptor.namespace,
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None


def test_success_publishes_existing_bytes_and_commit_retry_is_idempotent(repository, tmp_path):
    descriptor = _descriptor(repository)
    heads, identity, capture, _, _, _ = _candidate(repository, tmp_path, descriptor)
    before = list(tmp_path.glob("*.jsonl"))
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    assert list(tmp_path.glob("*.jsonl")) == before
    current = _head(heads, descriptor)
    assert current["version"] == 1
    assert heads.promote(identity, capture.artifacts) == "ALREADY_COMMITTED"
    assert _head(heads, descriptor) == current
    with repository.engine.connect() as connection:
        assert (
            connection.execute(
                select(heads.pins).where(heads.pins.c.attempt_id == identity.attempt_id)
            ).first()
            is None
        )


def test_same_revision_same_value_reuses_approved_files_with_new_freshness(repository, tmp_path):
    descriptor = _descriptor(repository)
    heads, identity, first, record, _, _ = _candidate(repository, tmp_path, descriptor)
    assert heads.promote(identity, first.artifacts) == "PUBLISHED"
    old = _head(heads, descriptor)
    heads, identity, second, _, _, loaded = _candidate(repository, tmp_path, descriptor)
    assert loaded == 0
    assert heads.promote(identity, second.artifacts) == "PUBLISHED"
    new = _head(heads, descriptor)
    assert new["content_digest"] == old["content_digest"] and new["version"] == 2
    assert new["source_collected_at"] > record["fetched_at"]
    with repository.engine.connect() as connection:

        def refs(key):
            return (
                connection.execute(
                    select(heads.refs.c.artifact_digest).where(
                        heads.refs.c.generation_id == key,
                    )
                )
                .scalars()
                .all()
            )

        assert refs(old["generation_id"]) == refs(new["generation_id"])


def test_newer_failed_current_does_not_block_older_successful_replay(repository, tmp_path):
    descriptor = _descriptor(repository)
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    heads, identity, initial, _, _, _ = _candidate(repository, tmp_path, descriptor, revision=base)
    assert heads.promote(identity, initial.artifacts) == "PUBLISHED"
    _candidate(
        repository, tmp_path, descriptor, revision=base + timedelta(hours=1), finish="failed"
    )
    _candidate(
        repository,
        tmp_path,
        descriptor,
        revision=base + timedelta(hours=2),
        value="9",
        finish="failed",
    )
    heads, identity, retry, _, _, loaded = _candidate(
        repository, tmp_path, descriptor, revision=base + timedelta(hours=1)
    )
    assert loaded == 0
    assert heads.promote(identity, retry.artifacts) == "PUBLISHED"
    assert _head(heads, descriptor)["data_revision_at"] == base + timedelta(hours=1)


@pytest.mark.parametrize("older", [True, False])
def test_older_revision_or_unproved_same_revision_correction_keeps_head(
    repository, tmp_path, older
):
    descriptor = _descriptor(repository)
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    heads, identity, first, _, _, _ = _candidate(repository, tmp_path, descriptor, revision=base)
    assert heads.promote(identity, first.artifacts) == "PUBLISHED"
    before = _head(heads, descriptor)
    heads, identity, second, _, _, _ = _candidate(
        repository,
        tmp_path,
        descriptor,
        revision=base - timedelta(hours=1) if older else base,
        value="2",
    )
    assert heads.promote(identity, second.artifacts) == ("SUPERSEDED" if older else "CONFLICT")
    assert _head(heads, descriptor) == before


def test_changed_owner_rejects_late_success(repository, tmp_path):
    descriptor = _descriptor(repository)
    heads, identity, capture, _, _, _ = _candidate(repository, tmp_path, descriptor)
    _candidate(repository, tmp_path, descriptor, finish=False)
    with pytest.raises(ExportArtifactError, match="소유권"):
        heads.promote(identity, capture.artifacts)
    assert _head(heads, descriptor) is None


def test_missing_file_cannot_publish_even_after_lifecycle_eligible(repository, tmp_path):
    descriptor = _descriptor(repository)
    heads, identity, capture, _, _, _ = _candidate(repository, tmp_path, descriptor)
    assert heads.lifecycle.reconcile(identity, capture.artifacts) == "ELIGIBLE"
    next(tmp_path.glob("*.jsonl")).unlink()
    with pytest.raises(OSError):
        heads.promote(identity, capture.artifacts)
    assert _head(heads, descriptor) is None
    with repository.engine.connect() as connection:
        assert (
            connection.execute(
                select(heads.pins).where(heads.pins.c.attempt_id == identity.attempt_id)
            ).first()
            is not None
        )


def test_known_success_survives_run_and_receipt_retention(repository, tmp_path):
    descriptor = _descriptor(repository)
    heads, identity, capture, record, _, _ = _candidate(repository, tmp_path, descriptor)
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"
    with repository.engine.begin() as connection:
        connection.execute(
            delete(heads.lifecycle.attempts).where(
                heads.lifecycle.attempts.c.attempt_id == identity.attempt_id
            )
        )
        connection.execute(
            delete(heads.metadata.tables["weather_sync_run_sources"]).where(
                heads.metadata.tables["weather_sync_run_sources"].c.run_id == record["run_id"]
            )
        )
        connection.execute(
            delete(heads.metadata.tables["weather_sync_runs"]).where(
                heads.metadata.tables["weather_sync_runs"].c.run_id == record["run_id"]
            )
        )
    assert heads.promote(identity, capture.artifacts) == "ALREADY_COMMITTED"
    with repository.engine.connect() as connection:
        assert (
            connection.execute(
                select(heads.refs).where(
                    heads.refs.c.generation_id == _head(heads, descriptor)["generation_id"]
                )
            ).first()
            is not None
        )
    with pytest.raises(ExportArtifactError):
        heads.promote(replace(identity, owner_id="wrong"), capture.artifacts)


def test_validation_has_no_database_connection(repository, tmp_path):
    descriptor = _descriptor(repository)
    heads, identity, capture, _, _, _ = _candidate(repository, tmp_path, descriptor)
    original = capture.artifacts.read
    calls = []

    def reader(*args, **kwargs):
        assert repository.engine.pool.checkedout() == 0
        calls.append(True)
        yield from original(*args, **kwargs)

    capture.artifacts.read = reader
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED" and calls


def test_descriptor_cannot_be_attached_retroactively_or_mutated(repository, tmp_path):
    descriptor = _descriptor(repository)
    heads, identity, capture, _, _, _ = _candidate(repository, tmp_path, descriptor)
    with pytest.raises(ExportArtifactError):
        heads.declare(identity, descriptor.model_copy(update={"normalizer_revision": "other"}))
    assert heads.promote(identity, capture.artifacts) == "PUBLISHED"


@pytest.mark.parametrize(
    "mutation", ["incomplete", "grid", "base", "duplicate", "future", "old_response"]
)
def test_invalid_source_proof_cannot_bind_or_publish(repository, tmp_path, mutation):
    descriptor = _descriptor(repository)

    def mutate(record):
        payload = record["payload"]
        row = payload["rows"][0]
        if mutation == "incomplete":
            payload["response_metadata"]["pagination"]["complete"] = False
        elif mutation == "grid":
            row["nx"] = 61
        elif mutation == "base":
            row["baseTime"] = "2300"
        elif mutation == "duplicate":
            payload["rows"].append(dict(row))
            payload["response_metadata"]["pagination"].update(
                expected_total_count=2, received_item_count=2
            )
        elif mutation == "future":
            record["fetched_at"] = datetime.now(UTC) - timedelta(days=2)
        elif mutation == "old_response":
            record["fetched_at"] = datetime.now(UTC) - timedelta(seconds=10)

    with pytest.raises(ExportArtifactError):
        _candidate(repository, tmp_path, descriptor, record_mutator=mutate)
    assert _head(ExportHeads(repository), descriptor) is None


def test_forged_normalized_value_cannot_be_captured(repository, tmp_path):
    from decimal import Decimal

    descriptor = _descriptor(repository)
    with pytest.raises(ExportArtifactError, match="source 계획"):
        _candidate(
            repository,
            tmp_path,
            descriptor,
            values_mutator=lambda values: [
                v.model_copy(update={"value_number": Decimal("999")}) for v in values
            ],
        )
    assert _head(ExportHeads(repository), descriptor) is None


def test_semantic_digest_ignores_retrieval_identity_but_keeps_value_policy():
    from kortravelweather.export_heads import semantic_point_digest

    point = dict(
        value_number="1.0000",
        target_at="2026-10-23T09:00:00+09:00",
        known_at="2026-10-23T00:00:01Z",
        source_record_key="first",
        value_id="a",
        normalization_version="kma-v1",
        unit="C",
    )
    same = dict(
        point,
        value_number="1",
        target_at="2026-10-23T00:00:00+00:00",
        known_at="2026-10-23T00:00:02Z",
        source_record_key="second",
        value_id="b",
    )
    assert semantic_point_digest(point) == semantic_point_digest(same)
    assert semantic_point_digest(point) != semantic_point_digest(dict(same, value_number="2"))
    assert semantic_point_digest(point) != semantic_point_digest(
        dict(same, normalization_version="kma-v2")
    )


def test_managed_artifact_guard_has_finite_wait(tmp_path):
    first, second = ExportArtifactStore(tmp_path), ExportArtifactStore(tmp_path)
    with (
        first.guard(),
        pytest.raises(ExportArtifactError, match="대기 상한"),
        second.guard(timeout_seconds=0.01),
    ):
        pytest.fail("다른 guard가 열린 동안 진입하면 안 됩니다.")


@pytest.mark.parametrize("offset", [timedelta(days=36500), timedelta(days=-1)])
def test_capture_cannot_forge_bound_freshness_but_original_fact_commits(
    repository, tmp_path, offset
):
    descriptor = _descriptor(repository)
    with pytest.raises(ExportArtifactError, match="source 계획"):
        _candidate(repository, tmp_path, descriptor, capture_clock_mutator=lambda at: at + offset)
    heads = ExportHeads(repository)
    assert _head(heads, descriptor) is None
    with repository.engine.connect() as connection:
        assert (
            connection.scalar(
                select(heads.lifecycle.chunks.c.chunk_key)
                .join(heads.plans, heads.plans.c.attempt_id == heads.lifecycle.chunks.c.attempt_id)
                .where(heads.plans.c.namespace == descriptor.namespace)
                .limit(1)
            )
            is None
        )
        assert (
            connection.scalar(
                select(heads.metadata.tables["weather_values"].c.value_number).where(
                    heads.metadata.tables["weather_values"].c.location_id
                    == descriptor.location_ids[0]
                )
            )
            == 1
        )


def test_bound_response_cannot_change_retrieval_clock_after_binding(repository, tmp_path):
    descriptor = _descriptor(repository)

    def mutate(record):
        record["fetched_at"] += timedelta(seconds=1)

    with pytest.raises(ExportArtifactError, match="source 계획"):
        _candidate(repository, tmp_path, descriptor, post_bind_record_mutator=mutate)
    assert _head(ExportHeads(repository), descriptor) is None


def test_bind_rejects_future_fetch_clock_before_recording_freshness(repository, tmp_path):
    descriptor = _descriptor(repository)

    def mutate(record):
        record["fetched_at"] += timedelta(days=36500)

    with pytest.raises(ExportArtifactError, match="선언 이전"):
        _candidate(repository, tmp_path, descriptor, record_mutator=mutate)
    assert _head(ExportHeads(repository), descriptor) is None


_ULTRA_SHORT_VALUES = {
    "LGT": "0", "POP": "0", "PTY": "0", "REH": "60", "RN1": "강수없음", "SKY": "1",
    "T1H": "12", "UUU": "0.5", "VEC": "200", "VVV": "-0.3", "WSD": "1.1",
}


def _bind_ultra_short(repository, row_base):
    """운영 초단기예보 응답 모양(6개 정시 × 11개 category)을 실제 bind 경로로 연결한다.

    ``row_base``는 SDK가 요청한 HH30 base를 받아 각 row의 (baseDate, baseTime)을 돌려준다.
    """
    from zoneinfo import ZoneInfo

    key = "ultra-" + uuid.uuid4().hex
    repository.create_location(
        WeatherLocation(
            location_id=key, name="초단기", latitude=37.5, longitude=127, nx=60, ny=127
        )
    )
    descriptor = ScopeDescriptor(
        dataset_key="kma_ultra_short_forecast",
        forecast_style="ultra_short",
        entity_type="weather_response",
        entity_id="grid-test:" + key,
        nx=60,
        ny=127,
        location_ids=(key,),
        normalizer_revision="kma-v1",
        winner_policy_revision="kma-base-v1",
        coverage_contract="kma-grid-forecast-v1",
    )
    heads = ExportHeads(repository)
    run = repository.start_sync_run(provider=descriptor.provider, dataset_key=descriptor.dataset_key)
    try:
        identity = repository.begin_export_attempt(
            run_id=run.run_id,
            scope_key="test",
            planned_sources=[descriptor.source_scope],
            execution_mode="native",
        )
        heads.lifecycle.claim(identity)
        heads.declare(identity, descriptor)
        now = datetime.now(UTC).astimezone(ZoneInfo("Asia/Seoul"))
        requested = (now - timedelta(hours=1)).replace(minute=30, second=0, microsecond=0)
        rows = []
        for index, hour in enumerate(range(1, 7)):
            at = requested.replace(minute=0) + timedelta(hours=hour)
            for category, value in _ULTRA_SHORT_VALUES.items():
                base_date, base_time = row_base(requested, index)
                rows.append({
                    "baseDate": base_date, "baseTime": base_time, "category": category,
                    "fcstDate": at.strftime("%Y%m%d"), "fcstTime": at.strftime("%H%M"),
                    "fcstValue": value, "nx": 60, "ny": 127,
                })
        record = dict(
            source_record_key="ultra-" + uuid.uuid4().hex,
            provider=descriptor.provider,
            dataset_key=descriptor.dataset_key,
            source_entity_type=descriptor.entity_type,
            source_entity_id=descriptor.entity_id,
            run_id=run.run_id,
            fetched_at=datetime.now(UTC),
            payload={
                "rows": rows,
                "response_metadata": {
                    "base_date": requested.strftime("%Y%m%d"),
                    "base_time": requested.strftime("%H%M"),
                    "request_params": {"nx": 60, "ny": 127},
                    "pagination": {
                        "expected_total_count": len(rows),
                        "received_item_count": len(rows),
                        "page_count": 1,
                        "complete": True,
                    },
                },
            },
        )
        heads.bind_kma_response(identity, record)
        with repository.engine.connect() as connection:
            return requested, connection.scalar(
                select(heads.plans.c.data_revision_at).where(
                    heads.plans.c.attempt_id == identity.attempt_id
                )
            )
    finally:
        repository.finish_sync_run(run.run_id, status="failed")


def test_ultra_short_binds_the_hh00_base_kma_announces_for_an_hh30_request(repository):
    """운영 실측(2026-10-27): 요청 base_time 0730에 모든 row가 baseTime 0700을 돌려준다."""
    requested, revision = _bind_ultra_short(
        repository,
        lambda requested, _: (requested.strftime("%Y%m%d"), requested.strftime("%H00")),
    )
    assert revision == requested.replace(minute=0).astimezone(UTC)


def test_ultra_short_same_base_as_request_still_binds(repository):
    requested, revision = _bind_ultra_short(
        repository,
        lambda requested, _: (requested.strftime("%Y%m%d"), requested.strftime("%H%M")),
    )
    assert revision == requested.astimezone(UTC)


def _previous_hour(requested, _):
    return requested.strftime("%Y%m%d"), (requested - timedelta(hours=1)).strftime("%H00")


def _mixed(requested, index):
    return requested.strftime("%Y%m%d"), requested.strftime("%H00" if index % 2 else "%H%M")


def _other_date(requested, _):
    return (requested - timedelta(days=1)).strftime("%Y%m%d"), requested.strftime("%H00")


@pytest.mark.parametrize("row_base", [_previous_hour, _mixed, _other_date])
def test_ultra_short_rejects_other_or_mixed_announced_bases(repository, row_base):
    with pytest.raises(ExportArtifactError, match="발표 base"):
        _bind_ultra_short(repository, row_base)


def test_promotion_rejects_receipt_clock_different_from_bound_source(repository, tmp_path):
    from sqlalchemy import update

    descriptor = _descriptor(repository)
    heads, identity, capture, record, _, _ = _candidate(repository, tmp_path, descriptor)
    with repository.engine.begin() as connection:
        connection.execute(
            update(heads.lifecycle.chunks)
            .where(heads.lifecycle.chunks.c.attempt_id == identity.attempt_id)
            .values(source_collected_at=record["fetched_at"] + timedelta(days=36500))
        )
    with pytest.raises(ExportArtifactError):
        heads.promote(identity, capture.artifacts)
    assert _head(heads, descriptor) is None
