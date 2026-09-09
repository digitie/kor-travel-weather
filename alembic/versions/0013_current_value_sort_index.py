"""index the ordering the nearby bundle reads.

0012 moved the sort keys onto the projection so the per-location ``LIMIT`` can
apply before the fact table is touched.  This index takes the remaining step:
with it the read is an index scan that stops at the limit instead of ranking a
location's whole slice.  Measured on a production-shaped replica, that is the
difference between 95.7 ms and 5.2 ms per bundle read.

Kept in its own revision because ``CREATE INDEX CONCURRENTLY`` cannot run in a
transaction.  Sharing a revision with 0012's column adds would commit those
before the build and leave the database half-applied -- with ``alembic_version``
still behind -- if the build were interrupted.

``_PREFERENCE`` below must express the same thing as the query's ORDER BY, or
PostgreSQL cannot use the index for it and the read goes back to ranking each
location's whole slice -- silently, with no error to notice.  Two tests hold
that together, and neither alone is enough:
``test_create_all_and_alembic_build_identical_marker_indexes`` compares the
index this revision builds against the one ``create_all`` builds from
``_CURRENT_PREFERENCE_EXPRESSION``, and
``test_the_bundle_ordering_is_served_by_its_index`` plans the real statement
against that index and fails if PostgreSQL has to sort.  The first binds this
string to the ORM's; the second binds the ORM's to the SQLAlchemy ``case()``
the query is actually built from, which is not a string and cannot be compared
to one.
"""

import sqlalchemy as sa

from alembic import op

revision = "0013_current_value_sort_index"
down_revision = "0012_current_value_sort_keys"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_weather_current_values_location_current"
_PREFERENCE = "(CASE WHEN forecast_style IN ('observed', 'nowcast') THEN 0 ELSE 1 END)"
_ORDERED_COLUMNS = (
    f"(location_id, {_PREFERENCE}, target_at DESC, known_at DESC NULLS LAST, "
    "source_record_key DESC NULLS LAST, value_id DESC)"
)


def _drop_if_invalid(bind: sa.engine.Connection, name: str) -> None:
    """Reclaim an index a failed concurrent build left behind.

    ``IF NOT EXISTS`` would otherwise skip the retry forever.  Only the
    ``indisvalid`` flag is consulted -- an index that merely shares the name is
    trusted, because comparing definitions reliably is harder than it looks and
    getting it wrong would drop a healthy index mid-deploy.
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


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
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
