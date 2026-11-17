"""DB artifact 누락·복구 계약에 쓰는 명시적 SQL fixture."""

from sqlalchemy import delete, select

from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore


def remove_artifact(repository, digest=None):
    """삭제된 DB artifact를 복구 시험용으로 보존한다."""
    store = DatabaseExportArtifactStore(repository)
    with repository.engine.begin() as connection:
        query = select(store.table)
        if digest is not None:
            query = query.where(store.table.c.digest == digest)
        row = dict(connection.execute(query).mappings().one())
        connection.execute(delete(store.table).where(store.table.c.digest == row["digest"]))
    return row


def restore_artifact(repository, row):
    """정확히 같은 불변 행을 되돌려 재시도 계약을 확인한다."""
    store = DatabaseExportArtifactStore(repository)
    with repository.engine.begin() as connection:
        connection.execute(store.table.insert().values(**row))
