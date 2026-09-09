"""partition the fact table by day so retention can drop instead of delete.

``weather_values`` is append-only and had no way to shed anything, so it grew to
23.3M rows and 33 GB, at which point the read path was doing 160 random reads a
second against a disk with nothing left to give.  Revision 0014 opened a door
for a batched ``DELETE``; this revision makes that door unnecessary.

Deleting three million rows a night writes a tombstone and a WAL record for
every one of them and then needs a vacuum to return the space.  Dropping a day's
partition is a catalog change: no scan, no row WAL, no vacuum, space back at
once.

## The partition key

``known_at``.  It is the axis retention is expressed in, the projection already
carries it, and it is never null -- the insert path falls back to
``collected_at``, which the model defaults, and production confirmed 0 nulls in
3.5M rows.  PostgreSQL requires the partition key in every unique constraint, so
the primary key becomes ``(value_id, known_at)`` and the identity constraint
gains ``known_at``.  Neither weakens anything: the logical identity already
fixes ``source_record_key``, and a source record has exactly one ``known_at``.

The current-value projection references the fact table, so its foreign key
becomes composite.  It already stores ``known_at`` -- 0012 put it there for
ordering and backfilled it -- and production confirmed every pointer agrees with
the fact it names, which is what makes it safe to key on.

## Why a rebuild rather than ATTACH

An existing table can be attached as a partition of a new parent, which avoids
copying.  It cannot be done here: the old primary key does not contain the
partition key, so the attached table's constraints would not satisfy the
parent's.  The rows have to move.  The copy is one sequential read and one
sequential write, which is the operation this disk is good at -- unlike the
random-access batched delete it replaces.

Ingestion must be stopped for this; ``deploy/n150.md`` says so and why.
"""

import sqlalchemy as sa

from alembic import op
from kortravelweather.partitions import (
    DEFAULT_PARTITION,
    ensure_default_partition,
    ensure_partitions,
)

revision = "0015_partition_weather_values"
down_revision = "0014_purge_escape_hatch"
branch_labels = None
depends_on = None

_LEGACY = "weather_values_unpartitioned"

#: ``ALTER TABLE ... RENAME`` moves the table and leaves every constraint name
#: where it was, so the new table cannot claim them.  These are moved aside
#: first, from one list, rather than the two hand-kept copies that would be a
#: CREATE and a rename drifting apart.  Plain indexes need no such treatment:
#: they are recreated only after the legacy table is dropped.
_CONSTRAINTS = (
    "weather_values_pkey",
    "uq_weather_values_identity",
    "ck_weather_values_has_value",
    "ck_weather_values_valid_window",
    "weather_values_location_id_fkey",
    "weather_values_source_record_key_fkey",
)

_COLUMNS = """
    value_id VARCHAR(64) NOT NULL,
    location_id VARCHAR(120) NOT NULL,
    provider VARCHAR(120) NOT NULL,
    dataset_key VARCHAR(160) NOT NULL,
    weather_domain VARCHAR(120) NOT NULL,
    forecast_style VARCHAR(40) NOT NULL,
    timeline_bucket VARCHAR(40),
    metric_key VARCHAR(80) NOT NULL,
    metric_name VARCHAR(200),
    source_metric_key VARCHAR(80),
    source_metric_name VARCHAR(200),
    value_number NUMERIC(14, 4),
    value_text TEXT,
    unit VARCHAR(32),
    severity VARCHAR(64),
    issued_at TIMESTAMP WITH TIME ZONE,
    valid_at TIMESTAMP WITH TIME ZONE,
    valid_from TIMESTAMP WITH TIME ZONE,
    valid_until TIMESTAMP WITH TIME ZONE,
    observed_at TIMESTAMP WITH TIME ZONE,
    target_at TIMESTAMP WITH TIME ZONE NOT NULL,
    known_at TIMESTAMP WITH TIME ZONE NOT NULL,
    normalization_version VARCHAR(40) NOT NULL,
    payload JSON NOT NULL DEFAULT '{}',
    collected_at TIMESTAMP WITH TIME ZONE NOT NULL,
    source_record_key VARCHAR(255) NOT NULL,
    CONSTRAINT weather_values_pkey PRIMARY KEY (value_id, known_at),
    CONSTRAINT uq_weather_values_identity UNIQUE (
        location_id, provider, dataset_key, weather_domain, forecast_style,
        metric_key, target_at, source_record_key, known_at
    ),
    CONSTRAINT ck_weather_values_has_value
        CHECK (value_number IS NOT NULL OR value_text IS NOT NULL),
    CONSTRAINT ck_weather_values_valid_window
        CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from),
    CONSTRAINT weather_values_location_id_fkey FOREIGN KEY (location_id)
        REFERENCES weather_locations (location_id) ON DELETE RESTRICT,
    CONSTRAINT weather_values_source_record_key_fkey FOREIGN KEY (source_record_key)
        REFERENCES weather_source_records (source_record_key) ON DELETE RESTRICT
"""

#: The pre-partition shape, for ``downgrade``: the key loses ``known_at``, which
#: only the partitioning required.
_UNPARTITIONED_COLUMNS = _COLUMNS.replace(
    "PRIMARY KEY (value_id, known_at)", "PRIMARY KEY (value_id)"
).replace(
    "metric_key, target_at, source_record_key, known_at",
    "metric_key, target_at, source_record_key",
)

#: Recreated on the parent, which propagates each to every partition.  Kept
#: byte-identical to the ORM's declarations, because ``create_all`` and this
#: revision both claim these names and a test compares the two schemas.
_INDEXES = (
    "CREATE INDEX ix_weather_values_location_time ON weather_values "
    "(location_id, valid_at, observed_at)",
    "CREATE INDEX ix_weather_values_location_target_known ON weather_values "
    "(location_id, target_at, known_at)",
    "CREATE INDEX ix_weather_values_dataset_metric ON weather_values "
    "(dataset_key, metric_key)",
    "CREATE INDEX ix_weather_values_marker_lookup ON weather_values "
    "(location_id, metric_key, known_at DESC NULLS LAST, "
    " source_record_key DESC NULLS LAST, value_id DESC) "
    "WHERE metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')",
    "CREATE INDEX ix_weather_values_marker_observed ON weather_values "
    "(location_id, metric_key, known_at DESC NULLS LAST, "
    " source_record_key DESC NULLS LAST, value_id DESC) "
    "WHERE metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY') "
    "AND forecast_style IN ('observed', 'nowcast')",
    "CREATE INDEX ix_weather_values_alert_lookup ON weather_values "
    "(location_id, target_at DESC) "
    "WHERE weather_domain = 'weather_alert' OR metric_key = 'ALERT'",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if _is_partitioned(bind):
        return

    span = bind.execute(
        sa.text("SELECT min(known_at)::date, max(known_at)::date FROM weather_values")
    ).one()

    op.execute(sa.text(f"ALTER TABLE weather_values RENAME TO {_LEGACY}"))
    _free_constraint_names(bind)
    # The projection points at the legacy table until the swap is complete.
    op.execute(
        sa.text(
            "ALTER TABLE weather_current_values "
            "DROP CONSTRAINT IF EXISTS weather_current_values_value_id_fkey"
        )
    )
    op.execute(
        sa.text(f"CREATE TABLE weather_values ({_COLUMNS}) PARTITION BY RANGE (known_at)")
    )
    ensure_default_partition(bind)
    if span[0] is not None:
        ensure_partitions(bind, start=span[0], end=span[1])

    _copy_rows(bind, source=_LEGACY, target="weather_values")
    op.execute(sa.text(f"DROP TABLE {_LEGACY}"))

    for statement in _INDEXES:
        op.execute(sa.text(statement))

    # The projection's ``known_at`` is half the new reference.  0012 backfilled
    # it and production confirmed every pointer agrees with its fact, so the
    # promotion validates rather than fails.
    op.execute(
        sa.text(
            "ALTER TABLE weather_current_values ALTER COLUMN known_at SET NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE weather_current_values "
            "ADD CONSTRAINT weather_current_values_value_fkey "
            "FOREIGN KEY (value_id, known_at) "
            "REFERENCES weather_values (value_id, known_at) ON DELETE RESTRICT"
        )
    )
    _install_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not _is_partitioned(bind):
        return
    op.execute(
        sa.text(
            "ALTER TABLE weather_current_values "
            "DROP CONSTRAINT IF EXISTS weather_current_values_value_fkey"
        )
    )
    op.execute(sa.text(f"ALTER TABLE weather_values RENAME TO {_LEGACY}"))
    op.execute(sa.text(f"CREATE TABLE weather_values ({_UNPARTITIONED_COLUMNS})"))
    _copy_rows(bind, source=_LEGACY, target="weather_values")
    op.execute(sa.text(f"DROP TABLE {_LEGACY} CASCADE"))
    for statement in _INDEXES:
        op.execute(sa.text(statement))
    op.execute(
        sa.text(
            "ALTER TABLE weather_current_values "
            "ADD CONSTRAINT weather_current_values_value_id_fkey "
            "FOREIGN KEY (value_id) REFERENCES weather_values (value_id) "
            "ON DELETE RESTRICT"
        )
    )
    _install_triggers(bind)


def _copy_rows(bind: sa.engine.Connection, *, source: str, target: str) -> None:
    """Copy by column name, never by position.

    ``INSERT ... SELECT *`` matches columns in physical order, and the physical
    order of a table built by fourteen migrations is not the order of a table
    written out in one statement.  It failed on ``payload``, loudly; it could
    equally have silently swapped two columns of the same type.
    """
    columns = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT attname FROM pg_attribute "
                "WHERE attrelid = CAST(:table AS regclass) "
                "AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
            ),
            {"table": source},
        )
    ]
    names = ", ".join(columns)
    op.execute(sa.text(f"INSERT INTO {target} ({names}) SELECT {names} FROM {source}"))


def _free_constraint_names(bind: sa.engine.Connection) -> None:
    """Move the renamed table's constraint names out of the new table's way."""
    for name in _CONSTRAINTS:
        exists = bind.execute(
            sa.text(
                # :table::regclass confuses text()'s parameter scan; the
                # cast has to be spelled out.
                "SELECT 1 FROM pg_constraint WHERE conname = :name "
                "AND conrelid = CAST(:table AS regclass)"
            ),
            {"name": name, "table": _LEGACY},
        ).scalar()
        if exists:
            op.execute(
                sa.text(f"ALTER TABLE {_LEGACY} RENAME CONSTRAINT {name} TO {name}_old")
            )


def _is_partitioned(bind: sa.engine.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class WHERE relname = 'weather_values' "
                "AND relkind = 'p'"
            )
        ).scalar()
    )


def _install_triggers(bind: sa.engine.Connection) -> None:
    """Re-attach immutability to the rebuilt table.

    A row trigger declared on a partitioned parent is propagated to every
    partition, including ones created later, so this is declared once.
    """
    op.execute(sa.text("DROP TRIGGER IF EXISTS weather_values_immutable ON weather_values"))
    op.execute(
        sa.text(
            "CREATE TRIGGER weather_values_immutable "
            "BEFORE UPDATE OR DELETE ON weather_values "
            "FOR EACH ROW EXECUTE FUNCTION weather_immutable_row()"
        )
    )
    op.execute(sa.text(f"ANALYZE {DEFAULT_PARTITION}"))
