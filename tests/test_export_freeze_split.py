"""current freeze의 선택 RR과 발급 TX 분리(호출 검토 A4·L8) 회귀.

- 선택 RR은 READ ONLY이고 INSERT가 없다. 발급 TX는 READ COMMITTED + ``lock_timeout``에서 선택
  RR이 계산한 값만 쓴다(catalog·승인·세대·주소를 다시 읽지 않는다).
- 선택 RR 뒤에 commit된 변경은 manifest에 섞이지 않는다(같은 snapshot의 manifest·digest).
- 선택 RR 동안 다른 요청이 발급한 같은 digest handle은 발급 TX가 보고 재사용한다.
- 발급 TX의 행 잠금 대기는 5초 문장 상한이 아니라 ``lock_timeout``(55P03)으로 끝난다.
"""

from time import monotonic

import pytest
from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import AddressTarget
from sqlalchemy import event, func, select
from sqlalchemy.exc import InternalError, OperationalError
from test_export_daily import daily_repository as daily_repository
from test_export_first_page_queries import approved_catalog as approved_catalog

from kortravelweather.export_capacity import (
    MANIFEST_STATE_RESERVE,
    MAX_MANIFEST_BYTES,
    bounded_manifest_digest,
)
from kortravelweather.export_selection_cache import ExportSelectionCache
from kortravelweather.export_snapshots import AtomicPinMissing, FrozenCurrentExports


def _reader(repo, store, **kwargs):
    return FrozenCurrentExports(
        repo, store, signing_key="freeze-split", source_inventory=True,
        expected_sources=(("python-kma-api", "kma_ultra_short_nowcast"),), **kwargs,
    )


def _transactions(engine, run):
    """run 동안의 transaction별 (격리 수준, 문장 목록)."""
    events = []

    def begin(_connection):
        events.append(("begin", None, None))

    def end(_connection):
        events.append(("end", None, None))

    def statement(connection, _cursor, sql, _parameters, _context, _many):
        events.append(("sql", sql, connection.get_execution_options().get("isolation_level")))

    listeners = [("begin", begin), ("commit", end), ("rollback", end),
                 ("before_cursor_execute", statement)]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        result = run()
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)
    transactions, current = [], None
    for kind, sql, isolation in events:
        if kind == "begin":
            current = {"isolation": None, "sql": []}
        elif kind == "end":
            if current is not None and current["sql"]:
                transactions.append(current)
            current = None
        elif current is not None:
            current["isolation"] = current["isolation"] or isolation
            current["sql"].append(sql)
    return result, transactions


def _snapshot_count(repo, reader):
    with repo.engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(reader.snapshots))


def test_selection_rr_is_read_only_and_the_issue_tx_writes_only_computed_values(
    approved_catalog,
):
    repo, store, _keys = approved_catalog
    reader = _reader(repo, store)
    frozen, transactions = _transactions(repo.engine, reader._freeze)
    plans = [tx for tx in transactions if "SET TRANSACTION READ ONLY" in tx["sql"]]
    issues = [tx for tx in transactions if any(
        sql.startswith("INSERT INTO weather_export_snapshots ") for sql in tx["sql"]
    )]
    assert len(plans) == len(issues) == 1 and plans[0] is not issues[0]
    plan, issue = plans[0], issues[0]
    assert plan["isolation"] == "REPEATABLE READ"
    assert plan["sql"][0] == "SET TRANSACTION READ ONLY"
    assert any("FROM weather_locations" in sql for sql in plan["sql"])
    assert not [sql for sql in plan["sql"]
                if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert issue["isolation"] == "READ COMMITTED"
    assert any("lock_timeout" in sql for sql in issue["sql"])
    # 발급 TX는 snapshot 표의 재사용 조회와 INSERT만 한다. 선택 입력은 다시 읽지 않는다.
    reads = [sql for sql in issue["sql"] if sql.lstrip().upper().startswith("SELECT")]
    assert reads and all("FROM weather_export_snapshots" in sql for sql in reads)
    assert not [sql for sql in issue["sql"] if any(table in sql for table in (
        "weather_locations", "reverse_address_entries", "weather_export_heads",
        "weather_export_scope_owners", "weather_export_generation_refs",
    ))]
    assert frozen["manifest_digest"] == bounded_manifest_digest(
        frozen["manifest"], MAX_MANIFEST_BYTES - MANIFEST_STATE_RESERVE,
    )


def test_selection_rr_rejects_writes(approved_catalog, monkeypatch):
    # READ ONLY는 DB가 보장한다. 선택 RR 안에서 쓰는 코드가 생기면 25006으로 드러난다.
    repo, store, _keys = approved_catalog
    reader = _reader(repo, store)
    original = FrozenCurrentExports._catalog_locations

    def writing(self, connection):
        connection.execute(reader.snapshots.delete())
        return original(self, connection)

    monkeypatch.setattr(FrozenCurrentExports, "_catalog_locations", writing)
    with pytest.raises(InternalError) as caught:
        reader._freeze()
    assert getattr(caught.value.orig, "sqlstate", None) == "25006"


def test_changes_committed_after_the_selection_rr_do_not_enter_the_manifest(
    approved_catalog, monkeypatch,
):
    repo, store, keys = approved_catalog
    reader = _reader(repo, store, selection_cache=ExportSelectionCache(), page_liveness=True)
    entries = reader.heads.metadata.tables["reverse_address_entries"]
    original = reader._issue_frozen
    seen = []

    def change_between(plan):
        # 선택 RR이 끝난 뒤, 발급 TX 전에 다른 transaction이 주소를 바꾼다.
        with repo.engine.begin() as connection:
            connection.execute(entries.insert().values(
                provider_id=VworldKakaoAddressResolver.provider_id, target_key=keys[0],
                longitude=127, latitude=37.5,
                coordinate_key=AddressTarget(keys[0], 127, 37.5).coordinate_key,
                status="ok", checked_at=func.now(), failures=0,
                payload={"address": {"legal_dong_code": "1111000000"},
                         "region": {"sido": "서울", "sigungu": "종로구"}},
            ))
        seen.append(plan)
        return original(plan)

    monkeypatch.setattr(reader, "_issue_frozen", change_between)
    frozen = reader._freeze()
    assert len(seen) == 1
    item = next(row for row in frozen["manifest"]["locations"] if row["location_id"] == keys[0])
    assert item["legal_dong_code"] is None  # 선택 RR의 값이다.
    with repo.engine.connect() as connection:
        stored = connection.execute(select(
            reader.snapshots.c.manifest, reader.snapshots.c.manifest_digest,
        ).where(reader.snapshots.c.snapshot_id == frozen["snapshot_id"])).one()
    assert stored.manifest == seen[0]["manifest"] == frozen["manifest"]
    assert stored.manifest_digest == seen[0]["digest"] == frozen["manifest_digest"]
    # 상태 판정도 같은 선택 RR의 것이고, 발급한 handle에 묶인다(#290의 freeze RR 판정).
    status = reader.__dict__.get("_freeze_status")
    assert status is not None and status[:2] == (frozen["snapshot_id"], frozen["manifest_digest"])
    assert status[2:] == seen[0]["status"]
    monkeypatch.undo()
    latest = _reader(repo, store)._freeze()
    assert latest["manifest_digest"] != frozen["manifest_digest"]
    assert next(row for row in latest["manifest"]["locations"]
                if row["location_id"] == keys[0])["legal_dong_code"] == "1111000000"


def test_a_same_digest_handle_issued_during_the_selection_rr_is_reused(
    approved_catalog, monkeypatch,
):
    repo, store, _keys = approved_catalog
    reader = _reader(repo, store)
    original = reader._issue_frozen
    issued = []

    def concurrent_issue(plan):
        # 선택 RR 동안 다른 요청이 같은 manifest의 handle을 먼저 발급했다.
        issued.append(_reader(repo, store)._freeze())
        return original(plan)

    monkeypatch.setattr(reader, "_issue_frozen", concurrent_issue)
    frozen = reader._freeze()
    assert issued and issued[0].get("_reused") is None
    assert frozen["snapshot_id"] == issued[0]["snapshot_id"] and frozen["_reused"] is True
    assert _snapshot_count(repo, reader) == 1


def test_issue_lock_wait_ends_at_the_lock_timeout_and_leaves_no_handle(approved_catalog):
    repo, store, _keys = approved_catalog
    reader = _reader(repo, store)
    generation = reader._freeze_plan()["generations"][0]
    generations = reader.heads.generations
    with repo.engine.connect() as holder, holder.begin():
        # ref INSERT의 FK 검사(FOR KEY SHARE)가 기다리게 세대 행을 잠근다.
        holder.execute(select(generations.c.generation_id).where(
            generations.c.generation_id == generation,
        ).with_for_update())
        started = monotonic()
        with pytest.raises(OperationalError) as caught:
            reader._freeze()
        elapsed = monotonic() - started
    assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    # 선택 RR(읽기)은 이 잠금을 기다리지 않고, 발급 TX는 5초 문장 상한 전에 끝난다.
    assert elapsed < 4
    assert _snapshot_count(repo, reader) == 0
    assert reader._freeze()["snapshot_id"]


def test_a_generation_deleted_after_the_selection_rr_reissues_instead_of_failing(
    approved_catalog,
):
    repo, store, _keys = approved_catalog
    reader = _reader(repo, store)
    plan = reader._freeze_plan()
    plan["generations"] = [*plan["generations"], "00000000-0000-0000-0000-000000000000"]
    with pytest.raises(AtomicPinMissing):
        reader._issue_frozen(plan)
    assert _snapshot_count(repo, reader) == 0
