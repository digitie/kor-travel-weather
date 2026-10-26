"""실DB 없이 보존 경계·정확pair SQL·유한 정리 프로토콜을 검증한다."""

import itertools
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from kortravelweather import repository as module
from kortravelweather.models import KST, WeatherValue
from kortravelweather.repository import WeatherRepository, _ResolvedFact
from kortravelweather.retention import (
    SHORT_FORECAST_CANDIDATES_SQL,
    ShortForecastKey,
    expired_short_forecast,
    made_progress,
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


_TIDS = itertools.count(1)


def row(value_id, known_at, valid_at, relation=16384):
    """실제 후보 SQL의 열 순서: value_id, known_at, valid_at, tableoid, ctid::text."""
    return (value_id, known_at, valid_at, relation, f"(0,{next(_TIDS)})")


class Session:
    """후보 선택·pair 포인터 삭제·ctid fact 삭제(RETURNING)를 흉내낸다."""

    def __init__(self, *, keys=None, remaining=False, lock=True, facts=None, select_error=None):
        self.keys = [row("id", NOW, CUTOFF - timedelta(hours=1))] if keys is None else keys
        self.remaining, self.lock = remaining, lock
        # facts: ctid 삭제가 실제로 돌려줄 최대 행 수(불일치 시험용). None이면 전부.
        self.facts = facts
        self.select_error = select_error
        self.statements = []
        self.committed = self.rolled_back = False

    def execute(self, statement, params=None):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.statements.append((sql, params, statement))
        if sql.startswith("SELECT value_id"):
            if self.select_error is not None:
                raise self.select_error
            return SimpleNamespace(all=lambda: self.keys)
        if sql.startswith("DELETE FROM weather_values "):
            tids = params["tids"].strip("{}").replace('"', "").split("),(")
            tids = {"(" + item.strip("()") + ")" for item in tids}
            hits = [
                (key[0], key[1]) for key in self.keys
                if key[3] == params["relation"] and key[4] in tids
            ]
            if self.facts is not None:
                hits = hits[: self.facts]
            return SimpleNamespace(all=lambda: hits, rowcount=len(hits))
        return SimpleNamespace(rowcount=1)

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
    expired = CUTOFF - timedelta(hours=1)
    session = Session(keys=[
        row("same", NOW, expired), row("same", NOW + timedelta(seconds=1), expired, relation=7),
    ])
    report = repo_with(monkeypatch, session).purge_expired_short_forecast(
        cutoff=CUTOFF, dry_run=False,
    )
    statements = [item for item in session.statements if item[0].startswith("DELETE")]
    sql = [item[0] for item in session.statements]
    assert statements[0][0].startswith("DELETE FROM weather_current_values ")
    assert "known_at = expired_short_keys.known_at" in statements[0][0]
    assert "value_id = expired_short_keys.value_id" in statements[0][0]
    # fact는 잠근 행의 물리 위치로 partition마다 지우며, PK를 다시 찾지 않는다.
    fact_deletes = statements[1:]
    assert [params["relation"] for _, params, _ in fact_deletes] == [16384, 7]
    for _, params, clause in fact_deletes:
        statement = str(clause)
        assert statement.startswith("DELETE FROM weather_values WHERE tableoid")
        assert "ctid = ANY(CAST(:tids AS tid[]))" in statement
        assert "known_at >= :low AND known_at <= :high" in statement
        assert "RETURNING value_id, known_at" in statement
        assert params["low"] == params["high"]
    assert all("weather_source_records" not in statement for statement in sql)
    assert "valid_at < :cutoff" in SHORT_FORECAST_CANDIDATES_SQL
    assert "known_at < :cutoff" not in SHORT_FORECAST_CANDIDATES_SQL
    # 잠긴 행을 건너뛰지 않고 lock_timeout 안에서 기다린다(위치 뒤에 건너뛴 행이 남지 않음).
    assert sql[3].endswith("FOR UPDATE") and "SKIP LOCKED" not in sql[3]
    assert report["facts_deleted"] == 2 and report["remaining"] is False
    # 덜 찬 batch는 남은행 확인 문장을 따로 실행하지 않는다.
    assert not any(statement.startswith("SELECT EXISTS") for statement in sql)
    assert report["last_key"] == ShortForecastKey(expired, NOW + timedelta(seconds=1), "same")
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


def _sql_error(sqlstate):
    """psycopg 예외처럼 ``sqlstate``를 가진 원인으로 SQLAlchemy OperationalError를 만든다."""
    name = {"57014": "QueryCanceled", "55P03": "LockNotAvailable"}.get(sqlstate, "UndefinedTable")
    cause = type(name, (Exception,), {"sqlstate": sqlstate})("fixture")
    return OperationalError("SELECT value_id ...", {}, cause)


@pytest.mark.parametrize(("sqlstate", "error_class"), [
    ("55P03", "LockNotAvailable"), ("57014", "QueryCanceled"),
])
def test_locked_or_slow_selection_defers_batch_not_false_complete(
    monkeypatch, sqlstate, error_class,
):
    session = Session(select_error=_sql_error(sqlstate), remaining=False)
    report = repo_with(monkeypatch, session).purge_expired_short_forecast(
        cutoff=CUTOFF, dry_run=False,
    )
    assert session.rolled_back and not session.committed
    assert report["deferred"] is True and report["remaining"] is None
    assert report["facts_deleted"] == report["matched_in_batch"] == 0
    assert report["last_key"] is None and report["error_class"] == error_class


def test_other_sql_errors_still_fail_the_batch(monkeypatch):
    session = Session(select_error=_sql_error("42P01"))
    with pytest.raises(OperationalError):
        repo_with(monkeypatch, session).purge_expired_short_forecast(
            cutoff=CUTOFF, dry_run=False,
        )
    assert session.rolled_back


def test_full_batch_checks_only_after_its_last_key(monkeypatch):
    expired = CUTOFF - timedelta(hours=2)
    keys = [row(f"id-{n}", NOW, expired) for n in range(3)]
    session = Session(keys=keys, remaining=True)
    after = ShortForecastKey(expired - timedelta(hours=1), NOW, "earlier")
    report = repo_with(monkeypatch, session).purge_expired_short_forecast(
        cutoff=CUTOFF, batch_size=3, dry_run=False, after=after,
    )
    _, select_params, select_clause = session.statements[3]
    assert "(valid_at, known_at, value_id) > (:after_valid_at" in str(select_clause)
    assert select_params["after_value_id"] == "earlier"
    exists_sql, exists_params, _ = session.statements[-1]
    assert exists_sql.startswith("SELECT EXISTS")
    assert "(valid_at, known_at, value_id) >" in exists_sql
    assert exists_params["after_value_id"] == "id-2" and exists_params["cutoff"] == CUTOFF
    assert report["remaining"] is True
    assert report["last_key"] == ShortForecastKey(expired, NOW, "id-2")


def test_head_selection_gets_its_own_budget_then_normal_statements(monkeypatch):
    session = Session(facts=1)
    repo_with(monkeypatch, session).purge_expired_short_forecast(
        cutoff=CUTOFF, dry_run=False, statement_timeout_ms=45000,
    )
    sql = [item[0] for item in session.statements]
    assert sql[0] == "SET LOCAL statement_timeout = '45000ms'"
    assert sql[3].startswith("SELECT value_id")
    # 머리 탐색 상한은 후보 선택에만 쓰고, 삭제 전에 정상 문장 예산으로 되돌린다.
    assert sql[4] == "SET LOCAL statement_timeout = '5000ms'"
    assert sql[5].startswith("DELETE FROM weather_current_values ")


@pytest.mark.parametrize("timeout", [4999, 60001, True, "5000"])
def test_statement_budget_refused_before_connection(monkeypatch, timeout):
    session = Session()
    with pytest.raises(ValueError):
        repo_with(monkeypatch, session).purge_expired_short_forecast(
            cutoff=CUTOFF, statement_timeout_ms=timeout,
        )
    assert session.statements == []


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
    # 위치가 없으므로 첫(유일한) 선택은 머리 탐색 상한을 받는다.
    assert calls == [{
        "cutoff": CUTOFF, "batch_size": 1000, "dry_run": True,
        "after": None, "statement_timeout_ms": 45000,
    }]
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


def _batch(last_key, *, remaining=True, matched=1000, deferred=False, error_class=None):
    return dict(
        matched_in_batch=matched, pointers_deleted=matched, facts_deleted=matched,
        remaining=remaining, deferred=deferred, last_key=last_key, error_class=error_class,
        elapsed_seconds=0.1,
    )


def test_runner_continues_each_batch_after_the_previous_last_key():
    first = ShortForecastKey(CUTOFF - timedelta(days=2), NOW - timedelta(days=3), "a")
    second = ShortForecastKey(CUTOFF - timedelta(days=1), NOW - timedelta(days=2), "b")
    reports = [_batch(first), _batch(second, remaining=False, matched=10)]
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        return reports.pop(0)

    result = run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=purge), cutoff=CUTOFF, dry_run=False,
    )
    # 첫 선택만 머리 탐색 상한, 이후는 앞 batch의 마지막 키 뒤에서 정상 예산으로 고른다.
    assert [call["after"] for call in calls] == [None, first]
    assert [call["statement_timeout_ms"] for call in calls] == [45000, 5000]
    assert result["status"] == "complete_snapshot" and result["facts_deleted"] == 1010
    assert result["resumed"] is False and result["backlog_lag_seconds"] == 0.0
    assert ShortForecastKey.from_metadata(result) == second


def test_runner_resumes_from_a_prior_run_without_head_scan():
    prior = ShortForecastKey(CUTOFF - timedelta(days=3), NOW - timedelta(days=4), "prior")
    last = ShortForecastKey(CUTOFF - timedelta(hours=30), NOW - timedelta(days=2), "z")
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        return _batch(last)

    result = run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=purge), cutoff=CUTOFF, dry_run=False,
        max_batches=2, after=prior,
    )
    assert calls[0]["after"] == prior and calls[0]["statement_timeout_ms"] == 5000
    assert calls[1]["after"] == last
    # 예산 소진이어도 지운 행이 있으면 진척이다. 지연은 위치의 대상시각 기준 하한이다.
    assert result["status"] == "budget_exhausted" and result["remaining"] is True
    assert result["resumed"] is True and made_progress(result)
    assert result["backlog_lag_seconds"] == timedelta(hours=30).total_seconds()
    assert result["resume_value_id"] == "z"


def test_runner_deferral_after_progress_keeps_remaining_and_position():
    first = ShortForecastKey(CUTOFF - timedelta(days=2), NOW - timedelta(days=3), "a")
    reports = [
        _batch(first),
        _batch(None, remaining=None, matched=0, deferred=True, error_class="QueryCanceled"),
    ]
    result = run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=lambda **_: reports.pop(0)),
        cutoff=CUTOFF, dry_run=False,
    )
    assert result["status"] == "deferred" and result["error_class"] == "QueryCanceled"
    assert result["remaining"] is True and result["facts_deleted"] == 1000
    assert ShortForecastKey.from_metadata(result) == first and made_progress(result)


def test_dry_run_never_moves_the_resume_position():
    seen = ShortForecastKey(CUTOFF - timedelta(days=2), NOW - timedelta(days=3), "a")
    result = run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=lambda **_: _batch(seen)),
        cutoff=CUTOFF, dry_run=True,
    )
    assert result["status"] == "dry_run"
    assert ShortForecastKey.from_metadata(result) is None and not made_progress(result)


@pytest.mark.parametrize("values", [
    {}, {"resume_valid_at": "x", "resume_known_at": "y", "resume_value_id": "z"},
    {"resume_valid_at": "2026-10-16T00:00:00", "resume_known_at": "2026-10-16T00:00:00+00:00",
     "resume_value_id": "naive"},
    {"resume_valid_at": None, "resume_known_at": None, "resume_value_id": None},
    {"resume_valid_at": "2026-10-16T00:00:00+00:00",
     "resume_known_at": "2026-10-16T00:00:00+00:00", "resume_value_id": "v" * 65},
])
def test_malformed_resume_metadata_restarts_from_head(values):
    assert ShortForecastKey.from_metadata(values) is None


def test_resume_metadata_round_trips_in_utc():
    key = ShortForecastKey(NOW.astimezone(KST), (NOW - timedelta(days=1)).astimezone(KST), "id")
    restored = ShortForecastKey.from_metadata(key.as_metadata())
    assert restored == key and restored.valid_at.tzinfo == UTC


@pytest.mark.parametrize(("remaining", "dry_run", "expected"), [
    (True, False, "deferred"),
    (False, False, "complete_snapshot"),
    (True, True, "dry_run"),
])
def test_runner_empty_selection_never_repeats_without_progress(remaining, dry_run, expected):
    calls = []

    def purge(**kwargs):
        calls.append(kwargs)
        return dict(matched_in_batch=0, pointers_deleted=0, facts_deleted=0,
                    remaining=remaining, deferred=False)

    result = run_short_forecast_retention(
        SimpleNamespace(purge_expired_short_forecast=purge),
        cutoff=CUTOFF, dry_run=dry_run,
    )
    assert result["status"] == expected
    assert result["batches"] == len(calls) == 1
    assert result["facts_deleted"] == 0
    assert result["remaining"] is remaining
