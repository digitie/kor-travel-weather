"""야간 이력 정리의 현재값 포인터·원천 기록 삭제를 keyset batch로 나눈다(2026-11-24 L4).

예전에는 둘 다 상한 없는 단일 DELETE였다. 지금은 batch마다 commit하고 batch마다 문장 10초·lock
1초 상한을 건다. 지우는 대상(만료 일자의 포인터, 아무것도 인용하지 않는 만료 원천)·advisory lock·
보고 수는 그대로다.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, func, select, text

from kortravelweather import repository as module
from kortravelweather.models import KST, ForecastStyle, WeatherLocation, WeatherValue
from kortravelweather.partitions import ensure_partitions, existing_partitions, partition_name
from kortravelweather.repository import (
    SourceRecordRow,
    WeatherCurrentValueRow,
    WeatherRepository,
    WeatherValueRow,
    pointer_purge_sql,
    source_purge_sql,
)

TEST_DATABASE_URL = os.environ.get(
    "KOR_TRAVEL_WEATHER_TEST_DATABASE_URL",
    "postgresql+psycopg://weather:weather@127.0.0.1:15432/weather_test",
)

LOCATION = "purge-batch"


@pytest.fixture
def repository():
    repo = WeatherRepository(TEST_DATABASE_URL)
    repo.create_schema()
    repo.upsert_location(
        WeatherLocation(location_id=LOCATION, name="정리 batch", latitude=37.5, longitude=127.0)
    )
    yield repo
    repo.engine.dispose()


@pytest.fixture
def fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "HISTORY_PURGE_BATCH_ATTEMPTS", 2)
    monkeypatch.setattr(module, "HISTORY_PURGE_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(module, "HISTORY_PURGE_LOCK_TIMEOUT_MS", 200)


def _publish(
    repository: WeatherRepository,
    suffix: str,
    known_at: datetime,
    *,
    short_valid_at: datetime | None = None,
) -> str:
    """``known_at``에 받은 원천 하나와 그것을 인용하는 fact 하나(독립 논리 지점)를 게시한다."""
    with repository.engine.begin() as connection:
        ensure_partitions(connection, start=known_at.date(), end=known_at.date())
    key = f"purge-batch-{suffix}"
    if short_valid_at is None:
        fields = dict(
            provider="p", dataset_key="d", weather_domain="weather",
            forecast_style=ForecastStyle.OBSERVED, metric_key=f"M{suffix}",
            target_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    else:
        fields = dict(
            provider="python-kma-api", dataset_key="kma_short_forecast",
            weather_domain="kma_short_forecast", forecast_style=ForecastStyle.SHORT,
            metric_key="TMP", target_at=short_valid_at, valid_at=short_valid_at,
            issued_at=known_at - timedelta(hours=1),
        )
    repository.record_source(
        source_record_key=key, provider=fields["provider"], dataset_key=fields["dataset_key"],
        source_entity_type="purge-fixture", source_entity_id=LOCATION,
        payload={"suffix": suffix}, fetched_at=known_at,
    )
    repository.upsert_values([
        WeatherValue(
            location_id=LOCATION, value_number=Decimal(1), source_record_key=key,
            known_at=known_at, collected_at=known_at, **fields,
        )
    ])
    return key


def _count(repository: WeatherRepository, row: type) -> int:
    with repository._session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(row)) or 0)


def _statements(repository: WeatherRepository) -> tuple[list[str], object]:
    seen: list[str] = []

    def before(_conn, _cursor, statement, _params, _context, _many):  # noqa: ANN001
        seen.append(" ".join(statement.split()))

    event.listen(repository.engine, "before_cursor_execute", before)
    return seen, before


def test_batch_sql_resumes_after_the_last_candidate_and_inlines_the_limit() -> None:
    first, resumed = pointer_purge_sql(resume=False), pointer_purge_sql(resume=True)
    assert ":after" not in first and "c.value_id > :after" in resumed
    assert f"LIMIT {module.HISTORY_PURGE_BATCH_ROWS}" in first
    assert ":batch_size" not in first
    sources = source_purge_sql(resume=True, held=" AND TRUE")
    assert "c.source_record_key > :after" in sources
    # 인용 확인 세 개와 떼어 둔 표 조건은 기존 DELETE 그대로 batch의 DELETE에 들어간다.
    assert (
        module.PURGE_SOURCE_RECORDS_SQL
        + " AND TRUE AND sr.source_record_key = ANY(ARRAY(SELECT candidate_key FROM candidates))"
    ) in sources


def test_pointers_and_sources_go_in_committed_keyset_batches(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "HISTORY_PURGE_BATCH_ROWS", 2)
    now = datetime.now(KST)
    aged = [_publish(repository, f"aged-{index}", now - timedelta(days=30)) for index in range(5)]
    fresh = _publish(repository, "fresh", now)

    seen, listener = _statements(repository)
    try:
        report = repository.purge_expired_history(retention_days=2)
    finally:
        event.remove(repository.engine, "before_cursor_execute", listener)

    assert report.pointers_deleted == 5 and report.sources_deleted == 5
    assert not report.pointers_deferred and not report.sources_deferred
    # 끝까지 읽었으므로 다음 실행은 처음부터다.
    assert report.sources_scanned == 5 and report.sources_resume_after is None
    assert partition_name((now - timedelta(days=30)).date()) in report.partitions_dropped
    with repository._session_factory() as session:
        left = set(session.scalars(select(SourceRecordRow.source_record_key)))
    assert left == {fresh} and not left & set(aged)
    assert _count(repository, WeatherCurrentValueRow) == 1

    pointer_batches = [
        i for i, sql in enumerate(seen) if "SELECT c.value_id AS candidate_key" in sql
    ]
    source_batches = [
        i for i, sql in enumerate(seen) if "SELECT c.source_record_key AS candidate_key" in sql
    ]
    # 만료 포인터·원천 5개씩을 2개씩: 후보 2·2·1 → 3 batch씩. 신선한 것은 후보가 아니다.
    assert len(pointer_batches) == 3 and len(source_batches) == 3
    lock = "SELECT pg_advisory_xact_lock(hashtext('weather_history_purge'))"
    starts = []
    for index in pointer_batches + source_batches:
        # batch마다 새 transaction: advisory lock을 먼저 기다려 잡고 그 뒤에 상한을 건다.
        start = max(i for i in range(index) if seen[i] == lock)
        starts.append(start)
        assert seen[start + 1 : start + 3] == [
            "SET LOCAL statement_timeout = '10000ms'",
            "SET LOCAL lock_timeout = '1000ms'",
        ]
    assert len(set(starts)) == len(starts)
    assert "%(after)s" not in seen[pointer_batches[0]]
    assert "c.value_id > %(after)s" in seen[pointer_batches[1]]


def test_a_retained_short_forecast_candidate_does_not_stall_the_keyset(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """지우지 않는 후보(최근6시간·미래 단기예보)도 위치를 넘기므로 batch 1행이어도 끝난다."""
    monkeypatch.setattr(module, "HISTORY_PURGE_BATCH_ROWS", 1)
    now = datetime.now(KST)
    old = now - timedelta(days=20)
    real_now = module.kst_now
    # 보존 단기예보는 그 시각에 받은 것처럼 게시해야 원 known_at을 지킨다(재입장하지 않게).
    monkeypatch.setattr(module, "kst_now", lambda: old)
    for index in range(3):
        _publish(repository, f"old-{index}", old)
    _publish(repository, "future", old, short_valid_at=now + timedelta(days=100))
    monkeypatch.setattr(module, "kst_now", real_now)
    _publish(repository, "fresh", now)
    with repository._session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(WeatherCurrentValueRow)
            .where(WeatherCurrentValueRow.known_at < now - timedelta(days=7))
        ) == 4

    deleted, complete = repository._purge_expired_pointers(now - timedelta(days=7))

    assert (deleted, complete) == (3, True)
    with repository._session_factory() as session:
        kept = set(session.scalars(select(WeatherCurrentValueRow.source_record_key)))
    assert kept == {"purge-batch-future", "purge-batch-fresh"}


def test_a_locked_pointer_defers_the_rest_and_keeps_the_expired_day_attached(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch, fast_retry: None
) -> None:
    """수집 CAS가 쥔 포인터에서 batch가 끝내 지면 앞 batch는 남고 만료 일자는 떼지 않는다.

    남은 포인터가 가리키는 fact를 떼어 내면 FK 검증이 그 포인터를 상한 없는 DELETE로 지우게
    된다. 그래서 그 밤은 일자를 연기(표시)하고 다음 실행이 정상적으로 지운다.
    """
    monkeypatch.setattr(module, "HISTORY_PURGE_BATCH_ROWS", 1)
    now = datetime.now(KST)
    aged_at = now - timedelta(days=30)
    for index in range(3):
        _publish(repository, f"aged-{index}", aged_at)
    day = partition_name(aged_at.date())
    with repository._session_factory() as session:
        ordered = list(session.scalars(
            select(WeatherCurrentValueRow.value_id).order_by(WeatherCurrentValueRow.value_id)
        ))
    blocker_engine = create_engine(TEST_DATABASE_URL)
    blocker = blocker_engine.connect()
    held = blocker.begin()
    try:
        # 두 번째 키의 행을 쥔다: 첫 batch는 commit되고 둘째 batch가 lock 상한에 진다.
        blocker.execute(
            text("SELECT 1 FROM weather_current_values WHERE value_id = :id FOR UPDATE"),
            {"id": ordered[1]},
        )
        report = repository.purge_expired_history(retention_days=2)
    finally:
        held.rollback()
        blocker.close()
        blocker_engine.dispose()

    assert report.pointers_deferred and report.pointers_deleted == 1
    assert day in report.partitions_deferred and day not in report.partitions_dropped
    with repository.engine.connect() as connection:
        assert day in {name for name, _ in existing_partitions(connection)}
    # 연기한 일자의 fact·원천은 그대로다(원천 정리는 인용 중인 것을 지우지 않는다).
    assert _count(repository, WeatherValueRow) == 3
    assert _count(repository, SourceRecordRow) == 3
    assert _count(repository, WeatherCurrentValueRow) == 2

    again = repository.purge_expired_history(retention_days=2)
    assert not again.pointers_deferred and again.pointers_deleted == 2
    assert day in again.partitions_dropped
    assert _count(repository, WeatherValueRow) == 0
    assert again.sources_deleted == 3


def test_a_source_batch_past_its_statement_limit_is_deferred_after_earlier_commits(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch, fast_retry: None
) -> None:
    """문장 상한(57014)에 진 batch는 rollback되고, 앞 batch의 commit은 남는다."""
    monkeypatch.setattr(module, "HISTORY_PURGE_BATCH_ROWS", 2)
    monkeypatch.setattr(module, "HISTORY_PURGE_STATEMENT_TIMEOUT_MS", 300)
    now = datetime.now(KST)
    for index in range(5):
        _publish(repository, f"source-{index}", now - timedelta(days=30))
    real = module.source_purge_sql
    calls: list[bool] = []

    def slow_after_first(*, resume: bool, held: str = "") -> str:
        calls.append(resume)
        if len(calls) == 1:
            return real(resume=resume, held=held)
        return "SELECT 0, NULL::text, 0 FROM pg_sleep(1)"

    monkeypatch.setattr(module, "source_purge_sql", slow_after_first)
    report = repository.purge_expired_history(retention_days=2)

    assert report.sources_deferred and report.sources_deleted == 2
    assert not report.pointers_deferred and report.pointers_deleted == 5
    # 첫 batch 1번 + 둘째 batch 2번 시도(fast_retry)
    assert calls == [False, True, True]
    assert _count(repository, SourceRecordRow) == 3
    # 진 batch의 시작 위치(첫 batch의 마지막 후보)를 넘긴다: 다음 실행은 그 batch부터 다시 한다.
    first_batch = sorted(f"purge-batch-source-{index}" for index in range(5))[:2]
    assert report.sources_scanned == 2
    assert report.sources_resume_after == first_batch[-1]

    monkeypatch.setattr(module, "source_purge_sql", real)
    again = repository.purge_expired_history(
        retention_days=2, sources_after=report.sources_resume_after
    )
    assert not again.sources_deferred and again.sources_deleted == 3
    assert again.sources_resume_after is None
    assert _count(repository, SourceRecordRow) == 0


def test_the_source_sweep_stops_on_its_budget_and_the_next_run_resumes(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run에 연결돼 지우지 못하는 원천이 쌓여도 한 실행은 예산만큼만 읽고 위치를 넘긴다.

    운영의 오래된 원천은 거의 다 run에 연결돼 지워지지 않으므로, 예산 없이 매일 처음부터 읽으면
    밤마다 이력 전체를 훑는다. 예산 0이면 batch 하나를 commit하고 멈춘다.
    """
    monkeypatch.setattr(module, "HISTORY_PURGE_BATCH_ROWS", 2)
    now = datetime.now(KST)
    aged_at = now - timedelta(days=30)
    keys = sorted(_publish(repository, f"swept-{index}", aged_at) for index in range(5))
    # 셋째 원천은 run 연결로 인용 중이라 남는다(운영의 대부분 원천과 같다).
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO weather_sync_runs (run_id, provider, dataset_key, status, started_at) "
            "VALUES ('sweep-run', 'p', 'd', 'success', now())"
        ))
        connection.execute(
            text(
                "INSERT INTO weather_sync_run_sources (run_id, source_record_key, recorded_at) "
                "VALUES ('sweep-run', :key, now())"
            ),
            {"key": keys[2]},
        )

    first = repository.purge_expired_history(retention_days=2, sources_budget_seconds=0)
    assert not first.sources_deferred
    assert first.sources_scanned == 2 and first.sources_deleted == 2
    assert first.sources_resume_after == keys[1]

    second = repository.purge_expired_history(
        retention_days=2, sources_after=first.sources_resume_after, sources_budget_seconds=0
    )
    # 이어 읽은 batch: run에 연결된 keys[2]는 후보지만 지우지 않는다.
    assert second.sources_scanned == 2 and second.sources_deleted == 1
    assert second.sources_resume_after == keys[3]

    third = repository.purge_expired_history(
        retention_days=2, sources_after=second.sources_resume_after, sources_budget_seconds=0
    )
    # 마지막 후보 하나: 덜 찬 batch라 끝이고, 다음 실행은 처음부터 다시 훑는다.
    assert third.sources_scanned == 1 and third.sources_deleted == 1
    assert third.sources_resume_after is None
    with repository._session_factory() as session:
        assert set(session.scalars(select(SourceRecordRow.source_record_key))) == {keys[2]}


def test_a_blank_resume_position_is_refused(repository: WeatherRepository) -> None:
    with pytest.raises(ValueError, match="sources_after"):
        repository.purge_expired_history(retention_days=2, sources_after="")


def test_other_errors_still_fail_the_run(
    repository: WeatherRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(repository, "error", datetime.now(KST) - timedelta(days=30))
    monkeypatch.setattr(
        module, "source_purge_sql",
        lambda *, resume, held="": "SELECT 1 / 0, NULL::text, 0",
    )
    with pytest.raises(Exception, match="division by zero"):
        repository.purge_expired_history(retention_days=2)


@pytest.mark.parametrize(
    ("statement", "index", "params"),
    [
        (pointer_purge_sql(resume=True), "weather_current_values_pkey", ("short_cutoff",)),
        (source_purge_sql(resume=True), "weather_source_records_pkey", ()),
    ],
)
def test_the_candidate_scan_walks_the_primary_key(
    repository: WeatherRepository, statement: str, index: str, params: tuple[str, ...]
) -> None:
    """후보는 기본키 순서로 ``:after`` 뒤부터 읽는다. 정렬 없이 batch 크기에서 멈출 수 있다.

    시험 표는 작아 비용으로는 고를 수 없으므로 순차 읽기·정렬을 끄고 "그 인덱스로 할 수 있는가"를
    묻는다. 지우는 쪽의 ``= ANY(후보 배열)``은 빈 표의 통계로는 계획을 고정할 수 없어 SQL 문장으로만
    확인한다(``test_batch_sql_resumes_after_the_last_candidate_and_inlines_the_limit``).
    """
    now = datetime.now(KST)
    with repository.engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET LOCAL enable_seqscan = off"))
            connection.execute(text("SET LOCAL enable_sort = off"))
            plan = "\n".join(
                line for (line,) in connection.execute(
                    text("EXPLAIN " + statement),
                    {"cutoff": now, "after": "", **dict.fromkeys(params, now)},
                )
            )
        finally:
            transaction.rollback()
    candidates = plan.split("CTE deleted")[0]
    assert re.search(rf"Index Scan using {index} on \S+ c\b", candidates), plan
    assert "Index Cond: (" in candidates and "> ''" in candidates, plan
    assert "Sort" not in candidates, plan
