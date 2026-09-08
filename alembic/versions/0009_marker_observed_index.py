"""index the observed/nowcast slice the map marker lookup actually reads.

Also repairs ``ix_weather_values_marker_lookup`` when an earlier
``Base.metadata.create_all`` built it with ascending trailing columns.  The
marker query walks revisions descending, so an ascending index forces a
per-location top-N sort instead of a bounded index read.
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_marker_observed_index"
down_revision = "0008_admin_login_rate_limits"
branch_labels = None
depends_on = None

_OBSERVED_INDEX = "ix_weather_values_marker_observed"
_LOOKUP_INDEX = "ix_weather_values_marker_lookup"
_METRIC_PREDICATE = "metric_key IN ('TEMP', 'T1H', 'TMP', 'WEATHER_CODE', 'SKY', 'PTY')"
_STYLE_PREDICATE = "forecast_style IN ('observed', 'nowcast')"
_ORDERED_COLUMNS = (
    "(location_id, metric_key, known_at DESC NULLS LAST, "
    "source_record_key DESC NULLS LAST, value_id DESC)"
)
# An index built with ascending trailing columns cannot serve the marker
# ORDER BY, so require the descending marker in the stored definition.
_REQUIRED_FRAGMENT = "known_at DESC"


def _existing_definition(bind: sa.engine.Connection, name: str) -> str | None:
    return bind.execute(
        sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": name},
    ).scalar()


def _is_invalid(bind: sa.engine.Connection, name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        ).scalar()
    )


def _drop_unusable(bind: sa.engine.Connection, name: str) -> None:
    """Drop the index when it is invalid or built with the wrong ordering.

    ``CREATE INDEX CONCURRENTLY`` leaves an unusable index behind when it
    fails, and ``IF NOT EXISTS`` would then skip the retry forever.  The same
    clause also silently accepts an index that merely shares the name, which is
    how a ``create_all`` development database keeps a mis-ordered index.
    """
    definition = _existing_definition(bind, name)
    if definition is None:
        return
    if _is_invalid(bind, name) or _REQUIRED_FRAGMENT not in definition:
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ``weather_values`` is append-only and can hold millions of rows, so build
    # without blocking API/Dagster reads during deploy.
    with op.get_context().autocommit_block():
        _drop_unusable(bind, _LOOKUP_INDEX)
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{_LOOKUP_INDEX} ON weather_values {_ORDERED_COLUMNS} "
                f"WHERE {_METRIC_PREDICATE}"
            )
        )
        _drop_unusable(bind, _OBSERVED_INDEX)
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{_OBSERVED_INDEX} ON weather_values {_ORDERED_COLUMNS} "
                f"WHERE {_METRIC_PREDICATE} AND {_STYLE_PREDICATE}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_OBSERVED_INDEX}"))
