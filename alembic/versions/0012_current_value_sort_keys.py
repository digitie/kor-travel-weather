"""carry the bundle sort keys on the current-value projection.

``_current_value_models_many`` ordered a location's slice by two columns that
live on ``weather_values`` (``known_at``, ``source_record_key``) mixed with
projection columns.  PostgreSQL therefore had to join before it could sort, so
the per-location ``LIMIT`` bounded only the output, not the work.  Measured on
production: one 100-location bundle read 4,284 projected rows per location and
did 425,263 primary-key lookups into the 28 GB fact table, and ``LIMIT 60`` cost
the same as ``LIMIT 200``.

Denormalising the two sort keys onto the pointer table lets the limit apply
before the fact table is touched.  The values describe the fact the pointer
names and are rewritten whenever the pointer moves.
"""

import sqlalchemy as sa

from alembic import op

revision = "0012_current_value_sort_keys"
down_revision = "0011_current_value_alert_index"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_current_values_location_current"
_PREFERENCE = "(CASE WHEN forecast_style IN ('observed', 'nowcast') THEN 0 ELSE 1 END)"
_ORDERED_COLUMNS = (
    f"(location_id, {_PREFERENCE}, target_at DESC, known_at DESC NULLS LAST, "
    "source_record_key DESC NULLS LAST, value_id DESC)"
)


def _drop_if_invalid(bind: sa.engine.Connection, name: str) -> None:
    """Reclaim an index a failed concurrent build left behind."""
    if bind.execute(
        sa.text(
            "SELECT 1 FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = :name AND NOT i.indisvalid"
        ),
        {"name": name},
    ).scalar():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.add_column(
        "weather_current_values",
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "weather_current_values",
        sa.Column("source_record_key", sa.String(length=255), nullable=True),
    )
    # Backfill from the fact each pointer already names. Batched by primary key
    # so one statement does not hold a transaction open over the whole table.
    op.execute(
        sa.text(
            "UPDATE weather_current_values cv "
            "SET known_at = wv.known_at, source_record_key = wv.source_record_key "
            "FROM weather_values wv "
            "WHERE wv.value_id = cv.value_id"
        )
    )
    # Every projected row points at a fact, and ``source_record_key`` is NOT
    # NULL there, so the backfill leaves no gaps.
    op.alter_column("weather_current_values", "source_record_key", nullable=False)

    with op.get_context().autocommit_block():
        _drop_if_invalid(bind, _INDEX_NAME)
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{_INDEX_NAME} ON weather_current_values {_ORDERED_COLUMNS}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
    op.drop_column("weather_current_values", "source_record_key")
    op.drop_column("weather_current_values", "known_at")
