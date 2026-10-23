"""실제 PG metadata→파일→응답/CAS 전체에서 EX writer를 배제한다."""

import multiprocessing
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event
from test_export_external import external_repository as external_repository
from test_export_mid_reader_postgres import _collect as collect_mid
from test_frozen_service_exports import _collect as collect_current
from test_frozen_service_exports import _collect_forecast as collect_forecast

from kortravelweather.export_artifacts import ExportArtifactError, ExportArtifactStore
from kortravelweather.export_daily_reader import DailySummaryExports
from kortravelweather.export_daily_store import refresh_daily_summaries
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_locations import FrozenLocationExports
from kortravelweather.export_mid_reader import FrozenMidForecastExports
from kortravelweather.export_snapshots import FrozenCurrentExports


def _writer_probe(root, commands, replies):
    store = ExportArtifactStore(Path(root))
    while commands.get() != "stop":
        try:
            with store.guard(timeout_seconds=0.05):
                pass
        except ExportArtifactError:
            replies.put(False)
        else:
            replies.put(True)


@contextmanager
def writer_probe(root):
    context = multiprocessing.get_context("spawn")
    commands, replies = context.Queue(), context.Queue()
    process = context.Process(target=_writer_probe, args=(str(root), commands, replies))
    process.start()

    def probe():
        commands.put("probe")
        return replies.get(timeout=10)

    try:
        yield probe
    finally:
        if process.is_alive():
            commands.put("stop")
            process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        commands.close()
        replies.close()
        assert process.exitcode == 0


class AuditedFiles(ExportArtifactStore):
    def __init__(self, root, repo, probe):
        super().__init__(root)
        self.repo, self.probe, self.depth, self.reads = repo, probe, 0, 0

    @contextmanager
    def guard(self, **kwargs):
        assert self.depth == 0, "공개 entry와 파일 helper가 guard를 중첩했습니다."
        with super().guard(**kwargs):
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1

    def read(self, *args, **kwargs):
        assert self.depth == 1
        assert self.repo.engine.pool.checkedout() == 0, "파일 IO 동안 PG 연결을 붙들었습니다."
        assert not self.probe(), "파일 IO 중 EX writer가 진입했습니다."
        self.reads += 1
        yield from super().read(*args, **kwargs)


@pytest.mark.parametrize("kind", ["current", "forecast", "mid", "reference", "daily"])
def test_actual_public_page_guards_before_sql_through_health_and_releases_pg_for_files(
    external_repository, tmp_path, kind,
):
    repo = external_repository
    if kind == "mid":
        collect_mid(repo, tmp_path)
    elif kind == "forecast":
        collect_forecast(repo, tmp_path)
    else:
        collect_current(repo, tmp_path)
        if kind == "daily":
            refresh_daily_summaries(repo, ExportArtifactStore(tmp_path))
    with writer_probe(tmp_path) as probe:
        files = AuditedFiles(tmp_path, repo, probe)
        options = {"signing_key": "reader-lifetime-offline"}
        reader = {
            "current": lambda: FrozenCurrentExports(repo, files, **options),
            "forecast": lambda: FrozenForecastExports(repo, files, style="ultra_short", **options),
            "mid": lambda: FrozenMidForecastExports(repo, files, **options),
            "reference": lambda: FrozenLocationExports(repo, files, **options),
            "daily": lambda: DailySummaryExports(repo, files, **options),
        }[kind]()
        observed = {"metadata": False, "after_file": False}

        def before_sql(_connection, _cursor, statement, _parameters, _context, _many):
            assert files.depth == 1, "snapshot/health SQL이 reader 수명 밖에서 실행됐습니다."
            if not observed["metadata"]:
                assert not probe(), "metadata SELECT 전에 EX writer가 진입했습니다."
                observed["metadata"] = True
            if files.reads and not observed["after_file"]:
                assert not probe(), "파일 읽기 후 health SQL 전에 EX writer가 진입했습니다."
                observed["after_file"] = True

        event.listen(repo.engine, "before_cursor_execute", before_sql)
        try:
            args = (
                ["current"] if kind == "current" else ["locations"] if kind == "reference" else []
            )
            page, _ = reader.page(*args, limit=1, cursor=None)
            assert page["items"] and observed["metadata"]
            if kind in {"current", "forecast", "mid"}:
                assert files.reads and observed["after_file"]
            if page["meta"]["next_cursor"]:
                reader.page(*args, limit=1, cursor=page["meta"]["next_cursor"])
        finally:
            event.remove(repo.engine, "before_cursor_execute", before_sql)
        assert files.depth == 0 and probe()


def test_actual_daily_reducer_retains_guard_through_checkpoint_cas_and_returns_pool_for_io(
    external_repository, tmp_path,
):
    repo = external_repository
    collect_current(repo, tmp_path)
    with writer_probe(tmp_path) as probe:
        files = AuditedFiles(tmp_path, repo, probe)
        observed = {"metadata": False, "cas": False}

        def before_sql(_connection, _cursor, statement, _parameters, _context, _many):
            assert files.depth == 1
            if not observed["metadata"]:
                assert not probe()
                observed["metadata"] = True
            if statement.lstrip().upper().startswith("UPDATE WEATHER_DAILY_SUMMARY_PROGRESS"):
                assert files.reads and not probe(), "최종 checkpoint CAS 전에 guard를 해제했습니다."
                observed["cas"] = True

        event.listen(repo.engine, "before_cursor_execute", before_sql)
        try:
            result = refresh_daily_summaries(repo, files)
        finally:
            event.remove(repo.engine, "before_cursor_execute", before_sql)
        assert result["processed"] == 2 and all(observed.values())
        assert files.depth == 0 and probe()
