"""실DB 없이 보존 경계·정확pair SQL·유한 정리 프로토콜을 검증한다."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from kortravelweather import repository as module
from kortravelweather.models import KST, WeatherValue
from kortravelweather.repository import WeatherRepository, _ResolvedFact
from kortravelweather.retention import (
    SHORT_FORECAST_CANDIDATES_SQL,
    expired_short_forecast,
    run_short_forecast_retention,
    short_forecast_cutoff,
)

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
CUTOFF = NOW - timedelta(hours=6)


def value(**changes):
    fields = dict(
        location_id="retention-test", provider="python-kma-api",
        dataset_key="kma_short_forecast", weather_domain="kma_short_forecast",
        forecast_style="short", metric_key="TMP", value_number=1,
        valid_at=CUTOFF - timedelta(microseconds=1),
        issued_at=NOW - timedelta(days=10), known_at=NOW, collected_at=NOW,
        source_record_key="test-source",
    )
    fields.update(changes)
    return WeatherValue(**fields)


@pytest.mark.parametrize(("instant", "expired"), [
    (CUTOFF - timedelta(microseconds=1), True), (CUTOFF, False),
    (CUTOFF + timedelta(microseconds=1), False), (NOW + timedelta(days=3), False),
    (None, False),
])
def test_strict_valid_time_boundary_and_future(instant, expired):
    assert expired_short_forecast(value(valid_at=instant), CUTOFF) is expired


def test_utc_kst_are_same_cutoff_and_naive_refused():
    assert short_forecast_cutoff(NOW.astimezone(KST)) == CUTOFF
    with pytest.raises(ValueError):
        short_forecast_cutoff(NOW.replace(tzinfo=None))


@pytest.mark.parametrize("changes", [
    {"provider": "other"}, {"dataset_key": "kma_ultra_short_forecast"},
    {"weather_domain": "other"}, {"forecast_style": "mid"},
    {"forecast_style": "observed"},
])
def test_only_exact_short_forecast(changes):
    assert not expired_short_forecast(value(**changes), CUTOFF)


def test_new_expired_and_replay_do_not_republish_and_collision_stays_strict():
    repo = object.__new__(WeatherRepository)
    added = []
    session = SimpleNamespace(add=added.append)
    model = value()
    fact = _ResolvedFact(model, "test-source", "same-id", model.valid_at, NOW, NOW)
    existing = {}
    assert repo._insert_value_session(session, fact, existing, short_cutoff=CUTOFF) is None
    assert not added and len(existing) == 1
    changed = replace(fact, value=model.model_copy(update={"value_number": 2}))
    with pytest.raises(ValueError, match="immutable weather fact"):
        repo._insert_value_session(session, changed, existing, short_cutoff=CUTOFF)
    existing = {}
    # 기존행은 원래 수집 때 정상 저장됐다고 구성한 뒤 정책 경계만 이동한다.
    assert repo._insert_value_session(session, fact, existing) is True
    assert repo._insert_value_session(session, fact, existing, short_cutoff=CUTOFF) is None
    changed = replace(fact, value=model.model_copy(update={"value_number": 2}))
    with pytest.raises(ValueError, match="immutable weather fact"):
        repo._insert_value_session(session, changed, existing, short_cutoff=CUTOFF)


class Session:
    def __init__(self, *, keys=None, remaining=False, lock=True, facts=1):
        self.keys = [("id", NOW)] if keys is None else keys
        self.remaining, self.lock, self.facts = remaining, lock, facts
        self.statements = []
        self.committed = self.rolled_back = False

    def execute(self, statement, params=None):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.statements.append((sql, params, statement))
        if sql.startswith("SELECT value_id"):
            return SimpleNamespace(all=lambda: self.keys)
        count = self.facts if sql.startswith("DELETE FROM weather_values ") else 1
        return SimpleNamespace(rowcount=count)

    def scalar(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params, statement))
        if sql == "SHOW statement_timeout":
            return "60s"
        if "FROM pg_settings" in sql:
            return 60000
        return self.lock if "pg_try_advisory" in sql else self.remaining

    @contextmanager
    def begin(self):
        try:
            yield self
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True


def repo_with(monkeypatch, session):
    monkeypatch.setattr(module, "kst_now", lambda: NOW)
    repo = object.__new__(WeatherRepository)
    repo._session_factory = session
    return repo


def test_exact_pair_deletes_pointers_before_facts_and_keeps_source(monkeypatch):
    session = Session(keys=[("same", NOW), ("same", NOW + timedelta(seconds=1))], facts=2)
    report = repo_with(monkeypatch, session).purge_expired_short_forecast(
        cutoff=CUTOFF, dry_run=False,
    )
    sql = [item[0] for item in session.statements]
    deletes = [item for item in sql if item.startswith("DELETE")]
    assert deletes[0].startswith("DELETE FROM weather_current_values ")
    assert deletes[1].startswith("DELETE FROM weather_values ")
    for statement in deletes:
        assert "known_at = expired_short_keys.known_at" in statement
        assert "value_id = expired_short_keys.value_id" in statement
    assert "known_at >=" in deletes[1] and "known_at <=" in deletes[1]
    assert all("weather_source_records" not in statement for statement in sql)
    assert "valid_at < :cutoff" in SHORT_FORECAST_CANDIDATES_SQL
    assert "known_at < :cutoff" not in SHORT_FORECAST_CANDIDATES_SQL
    assert "FOR UPDATE SKIP LOCKED" in sql[3]
    assert report["facts_deleted"] == 2 and report["remaining"] is False
    assert session.committed


def test_dryrun_readonly_and_zero_deletes(monkeypatch):
    session = Session(remaining=True)
    report = repo_with(monkeypatch, session).purge_expired_short_forecast(cutoff=CUTOFF)
    sql = [item[0] for item in session.statements]
    assert sql[0] == "SET TRANSACTION READ ONLY"
    assert not any(statement.startswith("DELETE") for statement in sql)
    assert not any("FOR UPDATE" in statement for statement in sql)
    assert report["matched_in_batch"] == 1 and report["facts_deleted"] == 0


def test_selected_count_mismatch_rolls_back_pointer_deletion(monkeypatch):
    session = Session(facts=0)
    with pytest.raises(RuntimeError, match="삭제 수"):
        repo_with(monkeypatch, session).purge_expired_short_forecast(
            cutoff=CUTOFF, dry_run=False,
        )
    assert session.rolled_back and not session.committed


def test_locked_candidate_is_not_false_complete(monkeypatch):
    session = Session(keys=[], remaining=True)
    report = repo_with(monkeypatch, session).purge_expired_short_forecast(
        cutoff=CUTOFF, dry_run=False,
    )
    assert report["remaining"] is True and report["facts_deleted"] == 0


def test_busy_purge_is_deferred_without_delete(monkeypatch):
    session = Session(lock=False)
    report = repo_with(monkeypatch, session).purge_expired_short_forecast(
        cutoff=CUTOFF, dry_run=False,
    )
    assert report["deferred"] is True and report["remaining"] is None
    assert not any(sql.startswith("DELETE") for sql, _, _ in session.statements)


@pytest.mark.parametrize("cutoff", [CUTOFF + timedelta(microseconds=1), NOW,
                                   CUTOFF.replace(tzinfo=None)])
def test_unsafe_cutoff_refused_before_connection(monkeypatch, cutoff):
    session = Session()
    with pytest.raises(ValueError):
        repo_with(monkeypatch, session).purge_expired_short_forecast(cutoff=cutoff)
    assert session.statements == []


def test_runner_budget_progress_and_no_automatic_failure_retry():
    reports = [
        dict(matched_in_batch=1, pointers_deleted=1, facts_deleted=1,
             remaining=True, deferred=False),
        dict(matched_in_batch=1, pointers_deleted=1, facts_deleted=1,
             remaining=False, deferred=False),
    ]
    events = []
    repo = SimpleNamespace(purge_expired_short_forecast=lambda **_: reports.pop(0))
    result = run_short_forecast_retention(
        repo, cutoff=CUTOFF, dry_run=False, on_progress=events.append,
    )
    assert result["facts_deleted"] == 2 and result["status"] == "complete_snapshot"
    assert [event["phase"] for event in events] == [
        "BATCH_INTENT", "BATCH_COMMITTED", "BATCH_INTENT", "BATCH_COMMITTED",
    ]
    assert len({event["operation_id"] for event in events}) == 1
    calls = []

    def fail(**_):
        calls.append(1)
        raise TimeoutError

    with pytest.raises(TimeoutError):
        run_short_forecast_retention(
            SimpleNamespace(purge_expired_short_forecast=fail), cutoff=CUTOFF, dry_run=False,
        )
    assert calls == [1]


@pytest.mark.parametrize("size", [0, -1, 1001, True, "100"])
def test_batch_size_refused_before_connection(monkeypatch, size):
    session = Session()
    with pytest.raises(ValueError):
        repo_with(monkeypatch, session).purge_expired_short_forecast(
            cutoff=CUTOFF, batch_size=size,
        )
    assert session.statements == []


def test_cli_defaults_dryrun_and_has_safe_progress(monkeypatch, capsys):
    from kortravelweather import cli

    calls = []
    disposed = []
    def purge(**kwargs):
        calls.append(kwargs)
        return dict(matched_in_batch=1, pointers_deleted=0, facts_deleted=0,
                    remaining=True, deferred=False)
    repository = SimpleNamespace(
        purge_expired_short_forecast=purge,
        engine=SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    monkeypatch.setattr(cli, "kst_now", lambda: NOW)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "repository_from_settings", lambda _: repository)
    assert cli.main(["purge-short-forecast"]) == 0
    assert calls == [{"cutoff": CUTOFF, "batch_size": 1000, "dry_run": True}]
    assert disposed == [True]
    output = capsys.readouterr().out
    assert "BATCH_INTENT" in output and "dry_run" in output
    assert "SHORT_FORECAST_RETENTION_RESULT" in output
    assert "password" not in output.lower()


def test_cli_unsafe_cutoff_never_constructs_repository(monkeypatch, capsys):
    from kortravelweather import cli

    monkeypatch.setattr(cli, "kst_now", lambda: NOW)
    def forbidden():
        raise AssertionError("settings must not be read")
    monkeypatch.setattr(cli, "get_settings", forbidden)
    assert cli.main(["purge-short-forecast", "--execute", "--cutoff", NOW.isoformat()]) == 1
    output = capsys.readouterr()
    assert "ValueError" in output.out and output.err == ""
    assert "settings must not be read" not in output.out


@pytest.mark.parametrize("name", [
    "weather_values_20260101", "weather_values_default", "weather_values_default_detached",
])
@pytest.mark.parametrize("retained", [True, None])
def test_partition_guard_retained_or_unknown_blocks_drop(name, retained):
    from kortravelweather.retention import ShortForecastPartitionRetained

    connection = Session(remaining=retained)
    with pytest.raises(ShortForecastPartitionRetained):
        WeatherRepository._preserve_short_forecast_partition(connection, name)
    sql = connection.statements[-1][0]
    assert "valid_at >= :short_cutoff" in sql
    assert "issued_at" not in sql


def test_partition_guard_database_error_is_not_delete_permission():
    class Broken(Session):
        def scalar(self, *args, **kwargs):
            raise TimeoutError("bounded")
    with pytest.raises(TimeoutError):
        WeatherRepository._preserve_short_forecast_partition(Broken(), "weather_values_20260101")
    with pytest.raises(ValueError):
        WeatherRepository._preserve_short_forecast_partition(Session(), 'weather_values_x";DROP')


def test_manual_default_delete_keeps_future_fact_and_exact_pointer(monkeypatch):
    from kortravelweather import default_partition as default

    calls = []
    monkeypatch.setattr(default, "_walk_delete", lambda engine, **kwargs: calls.append(kwargs) or 0)
    default.purge_expired_rows(None, cutoff=NOW, log=lambda _: None)
    assert len(calls) == 2
    assert "wv.known_at = weather_current_values.known_at" in calls[0]["predicate"]
    assert "wv.value_id = weather_current_values.value_id" in calls[0]["predicate"]
    assert "wv.valid_at >= :short_cutoff" in calls[0]["predicate"]
    assert "NOT (" in calls[1]["predicate"] and "valid_at >= :short_cutoff" in calls[1]["predicate"]
    assert all(call["params"]["short_cutoff"].tzinfo is not None for call in calls)


@pytest.mark.parametrize("operation", ["swap", "drop"])
def test_manual_default_guard_runs_before_any_destructive_ddl(monkeypatch, operation):
    from kortravelweather import default_partition as default
    from kortravelweather.retention import ShortForecastPartitionRetained

    calls = []
    monkeypatch.setattr(
        default, "lock_for_partition_ddl", lambda *args, **kwargs: calls.append("lock"),
    )
    monkeypatch.setattr(default, "lock_for_old_default_drop", lambda *args: calls.append("lock"))
    monkeypatch.setattr(default, "detached_default_present", lambda *args: True)
    monkeypatch.setattr(default, "drop_foreign_keys_into_facts",
                        lambda *args: pytest.fail("guard 이전 destructive DDL"))
    monkeypatch.setattr(default, "drop_detached_partition",
                        lambda *args: pytest.fail("guard 이전 DROP"))
    def hold(connection, name):
        calls.append(name)
        raise ShortForecastPartitionRetained(name)
    monkeypatch.setattr(WeatherRepository, "_preserve_short_forecast_partition", hold)
    with pytest.raises(ShortForecastPartitionRetained):
        if operation == "swap":
            default.swap_default(None, NOW)
        else:
            default.drop_old_default(None)
    assert calls[0] == "lock" and len(calls) == 2


def _protected_fact(clock, *, replayed=False, source="future-source", **changes):
    item = value(known_at=clock, collected_at=clock, valid_at=NOW + timedelta(days=10), **changes)
    return _ResolvedFact(item, source, source, item.valid_at, clock, clock, replayed)


class ReentrySession:
    def __init__(self, rows=()):
        self.rows = rows
        self.queries = []

    def scalars(self, statement):
        self.queries.append(str(statement.compile(dialect=postgresql.dialect())))
        return SimpleNamespace(all=lambda: list(self.rows))


@pytest.mark.parametrize(("clock", "replayed", "expected"), [
    (NOW - timedelta(days=31), False, NOW - timedelta(days=31)),
    (NOW - timedelta(days=31), True, NOW - timedelta(days=31)),
    (NOW - timedelta(days=20), False, NOW),
    (NOW - timedelta(days=20), True, NOW),
    (NOW - timedelta(days=1), False, NOW - timedelta(days=1)),
])
def test_protected_clock_routing_and_original_fields(clock, replayed, expected):
    fact = _protected_fact(clock, replayed=replayed)
    session = ReentrySession()
    floor = NOW - timedelta(days=30)
    bounds = [(NOW - timedelta(days=7), NOW + timedelta(days=1))]
    result = WeatherRepository._readmit_retained_short_session(
        session, [fact], {}, catalog=(floor, bounds), admitted_at=NOW, short_cutoff=CUTOFF,
    )[0]
    assert result.known_at == expected
    assert result.collected_at == clock
    assert result.value is fact.value
    assert fact.value.known_at == clock
    assert result.source_key == fact.source_key
    # 별도 재입장 처리 후 기존 forgotten 경로가 다시 버리지 않아야 한다.
    assert WeatherRepository._redate_forgotten_replays_session(
        session, [result], {}, NOW - timedelta(days=7), short_cutoff=CUTOFF,
    ) == [result]


def test_prior_retained_pair_is_reused_and_checked_not_retimed():
    clock = NOW - timedelta(days=20)
    fact = _protected_fact(clock, replayed=True)
    prior = SimpleNamespace(value_id=fact.value_id, known_at=NOW - timedelta(days=1),
                            collected_at=clock)
    session = ReentrySession([prior])
    stored = {}
    for admitted_at in [NOW, NOW + timedelta(hours=1)]:
        result = WeatherRepository._readmit_retained_short_session(
            session, [fact], stored, catalog=(NOW - timedelta(days=30), []),
            admitted_at=admitted_at, short_cutoff=CUTOFF,
        )[0]
        assert result.known_at == prior.known_at and result.collected_at == clock
    assert (fact.value_id, prior.known_at) in stored
    assert all("DISTINCT ON" in sql for sql in session.queries)


def test_non_short_clock_and_missing_current_partition_are_not_silently_changed():
    clock = NOW - timedelta(days=20)
    other = _protected_fact(clock, provider="other")
    session = ReentrySession()
    assert WeatherRepository._readmit_retained_short_session(
        session, [other], {}, catalog=(NOW - timedelta(days=30), []),
        admitted_at=NOW, short_cutoff=CUTOFF,
    ) == [other]
    assert not session.queries
    with pytest.raises(ValueError, match="partition"):
        WeatherRepository._readmit_retained_short_session(
            session, [_protected_fact(clock)], {}, catalog=(NOW - timedelta(days=30), []),
            admitted_at=NOW, short_cutoff=CUTOFF,
        )


def test_isolated_old_partition_does_not_hide_another_clock_gap():
    floor = NOW - timedelta(days=30)
    old = NOW - timedelta(days=20)
    gap = NOW - timedelta(days=10)
    bounds = [(old, old + timedelta(days=1)), (NOW - timedelta(days=3), NOW + timedelta(days=1))]
    first, second = _protected_fact(old, source="old"), _protected_fact(gap, source="gap")
    result = WeatherRepository._readmit_retained_short_session(
        ReentrySession(), [first, second], {}, catalog=(floor, bounds),
        admitted_at=NOW, short_cutoff=CUTOFF,
    )
    assert result[0].known_at == old and result[1].known_at == NOW


@pytest.mark.parametrize(("previous", "milliseconds", "cap"), [
    ("0", 0, 5000), ("60s", 60000, 5000), ("2s", 2000, 2000), ("50ms", 50, 50),
])
def test_partition_lookup_caps_then_restores_exact_previous_budget(previous, milliseconds, cap):
    class TimeoutSession(Session):
        def scalar(self, statement, params=None):
            if str(statement) == "SHOW statement_timeout":
                return previous
            if "FROM pg_settings" in str(statement):
                return milliseconds
            return super().scalar(statement, params)
    connection = TimeoutSession()
    WeatherRepository._preserve_short_forecast_partition(connection, "weather_values_20260101")
    assert any(sql == f"SET LOCAL statement_timeout = '{cap}ms'"
               for sql, _, _ in connection.statements)
    sql, params, _ = connection.statements[-1]
    assert "set_config" in sql and params == {"previous": previous}
