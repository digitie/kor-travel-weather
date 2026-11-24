"""공개 조회의 DB 연결 단절을 재시도 가능한 503으로 분류하는지 검증한다(B4)."""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient
from kortravelweather_api.app import create_app
from kortravelweather_api.db_errors import is_connection_loss, is_transient_db_error, sqlstate
from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)

from kortravelweather.read_performance import ReadTrace
from kortravelweather.settings import WeatherSettings


class _Orig(Exception):
    def __init__(self, sqlstate: str | None, message: str = "server detail must not leak") -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _connect_failure(detail: str) -> OperationalError:
    """psycopg 3 접속 실패 모양(SQLSTATE 없음, ``connection failed: ...`` 원문)."""
    return OperationalError(
        None, None,
        _Orig(None, f'connection failed: connection to server at "db", port 5432 failed: {detail}'),
    )


def _invalidated(error: DBAPIError) -> DBAPIError:
    error.connection_invalidated = True
    return error


class FailingRepository:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def create_schema(self) -> None:
        pass

    def region_locations(self):
        raise self.error

    def list_sync_runs(self, *args, **kwargs):
        raise self.error


def _client(error: BaseException) -> TestClient:
    return TestClient(
        create_app(
            WeatherSettings(environment="development", admin_token="test-only-admin-token"),
            FailingRepository(error),
        ),
        raise_server_exceptions=False,
    )


CONNECTION_LOSS = [
    pytest.param(OperationalError("SELECT 1", {}, _Orig(None)), id="closed-without-sqlstate"),
    pytest.param(OperationalError("SELECT 1", {}, _Orig("08006")), id="08006-connection-failure"),
    pytest.param(OperationalError("SELECT 1", {}, _Orig("08001")), id="08001-cannot-connect"),
    pytest.param(DBAPIError("SELECT 1", {}, _Orig("08003")), id="08003-no-connection"),
    pytest.param(OperationalError("SELECT 1", {}, _Orig("57P01")), id="57P01-admin-shutdown"),
    pytest.param(OperationalError("SELECT 1", {}, _Orig("57P02")), id="57P02-crash-shutdown"),
    pytest.param(OperationalError("SELECT 1", {}, _Orig("57P03")), id="57P03-cannot-connect-now"),
    pytest.param(
        _invalidated(InterfaceError("SELECT 1", {}, _Orig(None))), id="invalidated-interface",
    ),
    # 접속 단계 실패는 SQLSTATE가 없다. 기다리면 회복하는 원인은 단절로 본다.
    pytest.param(_connect_failure("Connection refused"), id="connect-refused"),
    pytest.param(
        OperationalError(None, None, _Orig(None, "connection timeout expired")),
        id="connect-timeout",
    ),
    pytest.param(
        _connect_failure("FATAL:  the database system is starting up"), id="connect-starting-up",
    ),
    pytest.param(
        _connect_failure("FATAL:  the database system is shutting down"),
        id="connect-shutting-down",
    ),
    pytest.param(
        _connect_failure("FATAL:  sorry, too many clients already"), id="connect-too-many-clients",
    ),
]

NOT_TRANSIENT = [
    pytest.param(SQLAlchemyError("postgresql://user:secret@db/weather"), id="bare"),
    pytest.param(ProgrammingError("SELECT 1", {}, _Orig("42703")), id="42703-defect"),
    pytest.param(IntegrityError("SELECT 1", {}, _Orig("23505")), id="23505-integrity"),
    # SQLSTATE가 채워진 경우의 분류다. 실제 접속 단계 인증 실패는 아래 connect-* 모양으로 온다.
    pytest.param(OperationalError("SELECT 1", {}, _Orig("28P01")), id="28P01-with-sqlstate"),
    pytest.param(OperationalError("SELECT 1", {}, _Orig("53100")), id="53100-disk-full"),
    pytest.param(InterfaceError("SELECT 1", {}, _Orig(None)), id="interface-not-invalidated"),
    # 접속 단계 설정 오류(SQLSTATE 없음)는 기다려도 같으므로 500으로 남는다.
    pytest.param(
        _connect_failure('FATAL:  password authentication failed for user "weather"'),
        id="connect-password",
    ),
    pytest.param(
        _connect_failure(
            'FATAL:  no pg_hba.conf entry for host "10.0.0.5", user "weather", '
            'database "weather", no encryption'
        ),
        id="connect-pg-hba",
    ),
    pytest.param(
        _connect_failure('FATAL:  database "weather" does not exist'), id="connect-missing-db",
    ),
    pytest.param(
        _connect_failure('FATAL:  role "weather" does not exist'), id="connect-missing-role",
    ),
    pytest.param(
        _connect_failure('FATAL:  role "weather" is not permitted to log in'),
        id="connect-nologin",
    ),
    pytest.param(
        _connect_failure('FATAL:  permission denied for database "weather"'),
        id="connect-no-connect-privilege",
    ),
    pytest.param(
        _connect_failure("fe_sendauth: no password supplied"), id="connect-no-password",
    ),
]


@pytest.mark.parametrize("error", CONNECTION_LOSS)
def test_public_read_connection_loss_is_retryable_503(error):
    response = _client(error).get("/v1/weather/regions")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "DB_UNAVAILABLE"
    assert "server detail" not in response.text
    assert "SELECT" not in response.text


@pytest.mark.parametrize("error", NOT_TRANSIENT)
def test_public_read_defects_stay_500(error):
    response = _client(error).get("/v1/weather/regions")
    assert response.status_code == 500
    assert "retry-after" not in response.headers
    assert "secret" not in response.text


@pytest.mark.parametrize("error", CONNECTION_LOSS[:2])
def test_admin_path_connection_loss_keeps_existing_500(error):
    """관리 경로는 이번 분류 범위가 아니다. 기존처럼 처리되지 않은 오류로 남는다."""
    client = _client(error)
    response = client.get(
        "/v1/admin/sync-runs", headers={"x-admin-token": "test-only-admin-token"},
    )
    assert response.status_code == 500
    assert "retry-after" not in response.headers


def test_connection_loss_is_traced_as_error_not_timeout(monkeypatch):
    outcomes: list[str] = []
    original = ReadTrace.finish

    def finish(self, *, not_modified=False, outcome="ok"):
        outcomes.append(outcome)
        return original(self, not_modified=not_modified, outcome=outcome)

    monkeypatch.setattr(ReadTrace, "finish", finish)
    client = _client(OperationalError("SELECT 1", {}, _Orig("57P01")))
    assert client.get("/v1/weather/regions").status_code == 503
    assert outcomes == ["error"]


@pytest.mark.parametrize("error", CONNECTION_LOSS)
def test_connection_loss_is_also_transient_for_service_exports(error):
    assert is_connection_loss(error)
    assert is_transient_db_error(error)


@pytest.mark.parametrize("state", ["57014", "55P03", "40P01", "40001", "53300"])
def test_contention_is_transient_but_not_connection_loss(state):
    error = DBAPIError("SELECT 1", {}, _Orig(state))
    assert is_transient_db_error(error)
    assert not is_connection_loss(error)


@pytest.mark.parametrize("error", NOT_TRANSIENT)
def test_defects_are_neither_transient_nor_connection_loss(error):
    assert not is_connection_loss(error)
    assert not is_transient_db_error(error)


def test_real_backend_termination_returns_retryable_503(repository, monkeypatch):
    """실제 PostgreSQL에서 연결이 강제로 끊긴(57P01) 공개 조회도 503이고 pool은 회복한다."""

    def terminated():
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT pg_terminate_backend(pg_backend_pid())"))
        return {"ready": False, "items": []}

    def healthy():
        with repository.engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        return {"ready": False, "items": []}

    monkeypatch.setattr(repository, "region_locations", terminated)
    client = TestClient(create_app(WeatherSettings(environment="development"), repository))
    response = client.get("/v1/weather/regions")
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "DB_UNAVAILABLE"
    assert response.headers["retry-after"] == "1"
    assert repository.engine.pool.checkedout() == 0
    monkeypatch.setattr(repository, "region_locations", healthy)
    assert client.get("/v1/weather/regions").status_code == 200


class ConnectingRepository(FailingRepository):
    """요청 안에서 실제 psycopg 접속을 시도하는 저장소."""

    def __init__(self, url: URL) -> None:
        self.engine = create_engine(url, connect_args={"connect_timeout": 2})

    def region_locations(self):
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ready": False, "items": []}


def _real_connect_client(url: URL) -> tuple[TestClient, ConnectingRepository]:
    repository = ConnectingRepository(url)
    client = TestClient(
        create_app(WeatherSettings(environment="development"), repository),
        raise_server_exceptions=False,
    )
    return client, repository


@pytest.mark.parametrize(
    ("change", "secret"),
    [
        pytest.param({"password": "kor-travel-wrong-password"}, "kor-travel-wrong-password",
                     id="wrong-password"),
        pytest.param({"database": "kor_travel_weather_missing_db"}, "kor_travel_weather_missing_db",
                     id="missing-database"),
    ],
)
def test_real_connect_misconfiguration_stays_500(weather_test_database_url, change, secret):
    """실제 driver의 접속 설정 오류는 SQLSTATE가 없어도 일시 장애(503)로 숨기지 않는다."""
    client, repository = _real_connect_client(make_url(weather_test_database_url).set(**change))
    try:
        with pytest.raises(OperationalError) as caught:
            repository.region_locations()
        assert sqlstate(caught.value) is None
        assert not is_connection_loss(caught.value)
        assert not is_transient_db_error(caught.value)
        response = client.get("/v1/weather/regions")
        assert response.status_code == 500
        assert "retry-after" not in response.headers
        assert secret not in response.text
    finally:
        repository.engine.dispose()


def test_real_refused_connect_is_retryable_503():
    """아무도 듣지 않는 port로의 접속(거부 또는 시간 초과)은 재시도 가능한 503이다."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = make_url(f"postgresql+psycopg://weather:weather@127.0.0.1:{port}/weather_test")
    client, repository = _real_connect_client(url)
    try:
        response = client.get("/v1/weather/regions")
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "DB_UNAVAILABLE"
        assert response.headers["retry-after"] == "1"
    finally:
        repository.engine.dispose()
