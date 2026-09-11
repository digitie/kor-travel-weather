"""index the columns ``purge_expired_history``'s "still cited?" check reads.

The purge deletes a ``weather_source_records`` row once nothing cites it any
more, checked with two correlated ``NOT EXISTS`` subqueries against
``weather_values.source_record_key`` and
``weather_sync_run_sources.source_record_key``. Neither table had an index
with that column leading -- it only ever appeared as a trailing column of a
composite index -- so both subqueries fell back to a full scan per candidate
row.

That scan ran inside the same transaction as the partition maintenance ahead
of it, so every lock the transaction had taken -- including the brief
exclusive lock ``drop_partitions_before`` needs to detach a partition -- was
held for as long as the scan ran. On a production ``weather_values`` with
enough rows, that was long enough to block every ingestion job that reads
``weather_locations``, and it kept doing so for as long as the transaction
ran because a transaction holds what it locked until it commits or rolls
back, not just for the one statement that needed it.

## Building on an already-partitioned table

``PostgreSQL refuses CREATE INDEX CONCURRENTLY targeted at a partitioned
table directly`` -- concurrent builds are a per-partition operation. The
documented way around that is the one this revision follows for
``weather_values``: declare the index ``ON ONLY`` the parent (instant,
metadata only, and left invalid), build it ``CONCURRENTLY`` on each existing
partition, then ``ATTACH`` each to the parent index. Once every partition is
attached PostgreSQL marks the parent valid on its own, and every partition
created afterwards inherits the index automatically, the same way a
partition inherits any other index declared on the parent.

``weather_sync_run_sources`` is a plain table, so the direct
``CREATE INDEX CONCURRENTLY`` this revision used to attempt works for it
unchanged.
"""

import sqlalchemy as sa

from alembic import op
from kortravelweather.partitions import existing_partitions

revision = "0016_purge_lookup_indexes"
down_revision = "0015_partition_weather_values"
branch_labels = None
depends_on = None

_PARENT_INDEX = "ix_weather_values_source_record_key"
_PLAIN_INDEX = "ix_weather_sync_run_sources_source_record_key"


def _drop_if_invalid(bind: sa.engine.Connection, name: str) -> None:
    """Reclaim an index a failed concurrent build left behind.

    ``IF NOT EXISTS`` would otherwise skip the retry forever. Only the
    ``indisvalid`` flag is consulted -- an index that merely shares the name
    is trusted, because comparing definitions reliably is harder than it
    looks and getting it wrong would drop a healthy index mid-deploy.
    """
    if bind.execute(
        sa.text(
            "SELECT 1 FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = :name AND NOT i.indisvalid"
        ),
        {"name": name},
    ).scalar():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))


def _is_attached(bind: sa.engine.Connection, child_index: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_inherits i "
                "JOIN pg_class parent ON parent.oid = i.inhparent "
                "JOIN pg_class child ON child.oid = i.inhrelid "
                "WHERE parent.relname = :parent AND child.relname = :child"
            ),
            {"parent": _PARENT_INDEX, "child": child_index},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        _drop_if_invalid(bind, _PLAIN_INDEX)
        op.execute(
            sa.text(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_PLAIN_INDEX} "
                "ON weather_sync_run_sources (source_record_key)"
            )
        )

        op.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS {_PARENT_INDEX} "
                "ON ONLY weather_values (source_record_key)"
            )
        )
        for partition, _first_day in existing_partitions(bind):
            child_index = f"{partition}_source_record_key_idx"
            if _is_attached(bind, child_index):
                continue
            _drop_if_invalid(bind, child_index)
            op.execute(
                sa.text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {child_index} "
                    f"ON {partition} (source_record_key)"
                )
            )
            op.execute(
                sa.text(f"ALTER INDEX {_PARENT_INDEX} ATTACH PARTITION {child_index}")
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_PLAIN_INDEX}"))
        # Dropping the parent index drops every attached partition index with
        # it; there is no per-partition CONCURRENTLY equivalent for DROP on a
        # partitioned index, only for building one.
        op.execute(sa.text(f"DROP INDEX IF EXISTS {_PARENT_INDEX}"))
