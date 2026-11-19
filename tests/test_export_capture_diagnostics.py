"""최초 capture 예외의 타입/코드만 남기고 원문·SQL·원천 키를 숨긴다."""

import logging
import os
from dataclasses import replace

import pytest
from kma.exceptions import KmaRequestError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from kortravelweather import repository as module
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_receipts import export_receipt_tables
from kortravelweather.repository import Base, WeatherRepository, WeatherValueRow

SECRET = "PRIVATE_SENTINEL_DO_NOT_LOG"


class DriverFailure(Exception):
    sqlstate = "55P03"


def _sql_error():
    return OperationalError("SELECT " + SECRET, {"serviceKey": SECRET}, DriverFailure(SECRET))


def _provider_error(code="22"):
    return KmaRequestError(
        SECRET, provider=SECRET, endpoint="https://invalid/?serviceKey=" + SECRET,
        result_code=code,
    )


@pytest.mark.parametrize("kind, expected", [
    ("plain", ("RuntimeError", "-", "-", "-")),
    ("sql", ("OperationalError", "DriverFailure", "55P03", "-")),
    ("cause", ("RuntimeError", "DriverFailure", "55P03", "-")),
    ("provider", ("KmaRequestError", "-", "-", "22")),
    ("provider_cause", ("RuntimeError", "KmaRequestError", "-", "23")),
])
def test_safe_capture_fields_only_use_typed_metadata(kind, expected):
    error = RuntimeError(SECRET)
    if kind == "sql":
        error = _sql_error()
    elif kind == "cause":
        error.__cause__ = _sql_error()
    elif kind == "provider":
        error = _provider_error()
    elif kind == "provider_cause":
        error.__cause__ = _provider_error("23")
    fields = module._capture_failure_diagnostics(error)
    assert fields == expected
    assert SECRET not in " ".join(fields)


@pytest.mark.parametrize("state", ["55p03", "５５P03", "55P03\n" + SECRET, SECRET, 55003])
def test_safe_capture_fields_reject_invalid_sqlstate(state):
    error = DriverFailure(SECRET)
    error.sqlstate = state
    assert module._capture_failure_diagnostics(error)[2] == "-"


@pytest.mark.parametrize("code", ["２２", 22, "2", "000", "22\n" + SECRET])
def test_safe_capture_fields_reject_invalid_sdk_result(code):
    assert module._capture_failure_diagnostics(_provider_error(code))[3] == "-"


def test_safe_capture_fields_do_not_trust_generic_result_code():
    error = RuntimeError(SECRET)
    error.result_code = "22"
    assert module._capture_failure_diagnostics(error)[3] == "-"


def test_safe_capture_fields_bound_cycles_and_never_format_exception():
    class HostileError(Exception):
        @property
        def sqlstate(self):
            raise RuntimeError(SECRET)

        @property
        def result_code(self):
            raise RuntimeError(SECRET)

        @property
        def orig(self):
            raise RuntimeError(SECRET)

        def __str__(self):
            raise AssertionError("exception string must not be read")

        def __repr__(self):
            raise AssertionError("exception repr must not be read")

    error = HostileError()
    error.__cause__ = error
    assert module._capture_failure_diagnostics(error) == ("HostileError", "-", "-", "-")
    invalid_type = type("Bad\n" + SECRET, (Exception,), {})
    assert module._capture_failure_diagnostics(invalid_type())[0] == "Exception"


@pytest.fixture(scope="module")
def repository():
    repo = WeatherRepository(os.environ["KOR_TRAVEL_WEATHER_TEST_DATABASE_URL"])
    repo.create_schema()
    yield repo
    repo.engine.dispose()


def _setup_capture(repository, tmp_path):
    from test_export_receipts import _capture, _input

    record, value = _input(repository)
    record, capture = _capture(repository, record, tmp_path)
    return record, value, replace(capture, artifacts=DatabaseExportArtifactStore(repository))


def _assert_safe_warning(caplog, record, capture, expected):
    warnings = [r for r in caplog.records if r.name == module.__name__
                and str(r.msg).startswith("weather_export_capture_failed ")]
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.exc_info is None and warning.stack_info is None
    assert all(type(arg) is str for arg in warning.args)
    assert warning.args == (capture.attempt.attempt_id, record["run_id"], *expected)
    assert SECRET not in caplog.text
    assert "source_record_key" not in warning.getMessage()


@pytest.mark.parametrize("kind", ["sql", "cause", "provider", "real_sql"])
def test_capture_failure_keeps_fact_and_failed_attempt_without_leaking(
    repository, tmp_path, monkeypatch, caplog, kind,
):
    record, value, capture = _setup_capture(repository, tmp_path)
    error = _provider_error() if kind == "provider" else _sql_error()
    expected = ("KmaRequestError", "-", "-", "22") if kind == "provider" else (
        "OperationalError", "DriverFailure", "55P03", "-",
    )
    if kind == "cause":
        wrapped = RuntimeError(SECRET)
        wrapped.__cause__ = error
        error = wrapped
        expected = ("RuntimeError", "DriverFailure", "55P03", "-")
    if kind == "real_sql":
        expected = ("ProgrammingError", "UndefinedTable", "42P01", "-")

    def fail(session, chunk):
        if kind == "real_sql":
            session.execute(module.text("SELECT * FROM capture_diagnostics_missing_relation"))
        raise error

    monkeypatch.setattr(capture.artifacts, "record_in_transaction", fail)
    caplog.set_level(logging.WARNING, logger=module.__name__)
    assert repository.ingest_batch(
        source_records=[record], values=[value], export_capture=capture,
    ) == 1
    _assert_safe_warning(caplog, record, capture, expected)
    attempts, chunks = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        state = connection.execute(select(attempts).where(
            attempts.c.attempt_id == capture.attempt.attempt_id,
        )).mappings().one()
        assert state["status"] == "failed" and state["error_code"] == "capture_failed"
        assert connection.scalar(select(WeatherValueRow.value_id).where(
            WeatherValueRow.location_id == value.location_id,
        )) is not None
        assert connection.execute(select(chunks).where(
            chunks.c.attempt_id == capture.attempt.attempt_id,
        )).first() is None


def test_capture_cleanup_failure_cannot_replace_original_diagnostic(
    repository, tmp_path, monkeypatch, caplog,
):
    record, value, capture = _setup_capture(repository, tmp_path)

    def fail(session, chunk):
        raise _provider_error()

    def cleanup_fail(*args):
        raise DriverFailure(SECRET)

    monkeypatch.setattr(capture.artifacts, "record_in_transaction", fail)
    monkeypatch.setattr(repository, "_fail_export_capture", cleanup_fail)
    caplog.set_level(logging.WARNING, logger=module.__name__)
    assert repository.ingest_batch(
        source_records=[record], values=[value], export_capture=capture,
    ) == 1
    _assert_safe_warning(caplog, record, capture, ("KmaRequestError", "-", "-", "22"))
    assert "weather_export_failure_record_unavailable" in caplog.text
    attempts, chunks = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        state = connection.execute(select(attempts).where(
            attempts.c.attempt_id == capture.attempt.attempt_id,
        )).mappings().one()
        # 실패 표시에 실패한 기존 계약: preparing이지만 receipt가 없어 공개할 수 없다.
        assert state["status"] == "preparing" and state["error_code"] is None
        assert connection.execute(select(chunks).where(
            chunks.c.attempt_id == capture.attempt.attempt_id,
        )).first() is None
        assert connection.scalar(select(WeatherValueRow.value_id).where(
            WeatherValueRow.location_id == value.location_id,
        )) is not None


@pytest.mark.parametrize("failure", ["logger", "helper", "type_metadata"])
def test_capture_diagnostic_failure_never_skips_original_cleanup(
    repository, tmp_path, monkeypatch, caplog, failure,
):
    record, value, capture = _setup_capture(repository, tmp_path)
    original = RuntimeError(SECRET)
    metadata = {"armed": True}
    if failure == "type_metadata":
        class BrokenType(type):
            def __getattribute__(cls, name):
                if name == "__name__" and metadata["armed"]:
                    metadata["armed"] = False
                    raise RuntimeError(SECRET)
                return super().__getattribute__(name)

        class CaptureFailure(Exception, metaclass=BrokenType):
            pass

        original = CaptureFailure(SECRET)

    def fail(session, chunk):
        raise original

    def diagnostic_fail(*args, **kwargs):
        raise RuntimeError(SECRET)

    cleanup_calls = []
    cleanup = repository._fail_export_capture

    def original_cleanup(*args):
        cleanup_calls.append(args[-1])
        return cleanup(*args)

    monkeypatch.setattr(capture.artifacts, "record_in_transaction", fail)
    monkeypatch.setattr(repository, "_fail_export_capture", original_cleanup)
    if failure == "logger":
        monkeypatch.setattr(module.logger, "warning", diagnostic_fail)
    elif failure == "helper":
        monkeypatch.setattr(module, "_capture_failure_diagnostics", diagnostic_fail)
    caplog.set_level(logging.WARNING, logger=module.__name__)
    assert repository.ingest_batch(
        source_records=[record], values=[value], export_capture=capture,
    ) == 1
    assert cleanup_calls == ["capture_failed"]
    assert SECRET not in caplog.text
    if failure == "type_metadata":
        assert metadata["armed"] is False
    attempts, chunks = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        state = connection.execute(select(attempts).where(
            attempts.c.attempt_id == capture.attempt.attempt_id,
        )).mappings().one()
        assert state["status"] == "failed" and state["error_code"] == "capture_failed"
        assert connection.scalar(select(WeatherValueRow.value_id).where(
            WeatherValueRow.location_id == value.location_id,
        )) is not None
        assert connection.execute(select(chunks).where(
            chunks.c.attempt_id == capture.attempt.attempt_id,
        )).first() is None


def test_capture_diagnostic_failure_does_not_mask_original_inactive_error(
    repository, tmp_path, monkeypatch, caplog,
):
    from sqlalchemy.orm import Session, sessionmaker

    record, value, capture = _setup_capture(repository, tmp_path)
    original = RuntimeError("original-" + SECRET)
    sessions = []
    cleanup_calls = []

    class InactiveAfterDiagnostic(Session):
        @property
        def is_active(self):
            if getattr(self, "_diagnostic_test_inactive", False):
                return False
            return super().is_active

    def fail(session, chunk):
        sessions.append(session)
        raise original

    def diagnostic_fail(*args, **kwargs):
        # Savepoint 복구 후 기존 inactive 분기의 원본 재전파를 실제 TX로 확인한다.
        sessions[-1]._diagnostic_test_inactive = True
        raise RuntimeError("diagnostic-" + SECRET)

    monkeypatch.setattr(repository, "_session_factory", sessionmaker(
        repository.engine, class_=InactiveAfterDiagnostic, expire_on_commit=False,
    ))
    monkeypatch.setattr(capture.artifacts, "record_in_transaction", fail)
    monkeypatch.setattr(module.logger, "warning", diagnostic_fail)
    monkeypatch.setattr(
        repository, "_fail_export_capture", lambda *args: cleanup_calls.append(args),
    )
    with pytest.raises(RuntimeError) as caught:
        repository.ingest_batch(source_records=[record], values=[value], export_capture=capture)
    assert caught.value is original
    assert not cleanup_calls and SECRET not in caplog.text
    attempts, chunks = export_receipt_tables(Base.metadata)
    with repository.engine.connect() as connection:
        state = connection.execute(select(attempts).where(
            attempts.c.attempt_id == capture.attempt.attempt_id,
        )).mappings().one()
        assert state["status"] == "preparing" and state["error_code"] is None
        assert connection.scalar(select(WeatherValueRow.value_id).where(
            WeatherValueRow.location_id == value.location_id,
        )) is None
        assert connection.execute(select(chunks).where(
            chunks.c.attempt_id == capture.attempt.attempt_id,
        )).first() is None


def test_safe_capture_fields_reject_type_name_string_subclass_in_logger(caplog):
    class SecretName(str):
        def __str__(self):
            return SECRET

    class NameMetadata(type):
        def __getattribute__(cls, name):
            if name == "__name__":
                return SecretName("OrdinaryError")
            return super().__getattribute__(name)

    class CaptureFailure(Exception, metaclass=NameMetadata):
        pass

    fields = module._capture_failure_diagnostics(CaptureFailure())
    caplog.set_level(logging.WARNING, logger=module.__name__)
    module.logger.warning("error_type=%s", fields[0])
    assert SECRET not in caplog.text
    assert fields == ("Exception", "-", "-", "-")
    assert type(fields[0]) is str
    assert caplog.records[-1].getMessage() == "error_type=Exception"
