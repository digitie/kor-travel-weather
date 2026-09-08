"""carry the bundle sort keys on the current-value projection.

``_current_value_models_many`` ordered a location's slice by two columns that
live on ``weather_values`` (``known_at``, ``source_record_key``) mixed with
projection columns.  PostgreSQL therefore had to join before it could sort, so
the per-location ``LIMIT`` bounded only the output, not the work.  Measured on
production: one 100-location bundle read 4,284 projected rows per location and
did 425,263 primary-key lookups into the 28 GB fact table, and ``LIMIT 60`` cost
the same as ``LIMIT 200``.

Denormalising the two sort keys onto the pointer table lets the limit apply
before the fact table is touched.

## Why this revision is shaped the way it is

Adding a nullable column is a catalog-only change, so the ``ACCESS EXCLUSIVE``
lock it takes is held for milliseconds and no rewrite happens.  The backfill
then runs as ordinary ``UPDATE`` batches, which take only row locks: readers are
never blocked.  Doing both in one statement would instead hold ``ACCESS
EXCLUSIVE`` across a sequential scan of the 28 GB fact table, and every reader --
and the whole public API, which waits on this migration -- would block for the
duration.

The batches are resumable: each one selects rows that still have no sort key, so
an interrupted run simply continues where it stopped.  The column adds use
``IF NOT EXISTS`` for the same reason.  Alembic writes ``alembic_version`` only
after ``upgrade()`` returns, so anything committed before that point has to be
safe to repeat.

The columns stay nullable.  Compose starts this migration while the previous
release is still serving and ingesting, and that release writes pointer rows
without the new columns; a ``NOT NULL`` here would fail those inserts and break
collection mid-deploy.  Rows written in that window sort last within their group
(``nullslast``) and gain their keys with the logical point's next revision.

The index that makes the new ordering index-only is a separate revision, so a
failure there cannot strand this one half-applied.
"""

import sqlalchemy as sa

from alembic import op

revision = "0012_current_value_sort_keys"
down_revision = "0011_current_value_alert_index"
branch_labels = None
depends_on = None

_BATCH_ROWS = 50_000
# 2,887,417 rows on production at the time of writing; the cap only exists so a
# pathological loop cannot run forever.
_MAX_BATCHES = 500


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "ALTER TABLE weather_current_values "
                "ADD COLUMN IF NOT EXISTS known_at TIMESTAMP WITH TIME ZONE"
            )
        )
        op.execute(
            sa.text(
                "ALTER TABLE weather_current_values "
                "ADD COLUMN IF NOT EXISTS source_record_key VARCHAR(255)"
            )
        )

        for _ in range(_MAX_BATCHES):
            result = bind.execute(
                sa.text(
                    "UPDATE weather_current_values cv "
                    "SET known_at = wv.known_at, "
                    "    source_record_key = wv.source_record_key "
                    "FROM weather_values wv "
                    "WHERE wv.value_id = cv.value_id "
                    "  AND cv.value_id IN ("
                    "    SELECT value_id FROM weather_current_values "
                    "    WHERE source_record_key IS NULL LIMIT :batch"
                    "  )"
                ),
                {"batch": _BATCH_ROWS},
            )
            if result.rowcount == 0:
                break


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Deploy the previous release before running this: the current code reads
    # and writes both columns, so dropping them under a running API turns every
    # bundle read into an UndefinedColumn error.
    op.execute(
        sa.text(
            "ALTER TABLE weather_current_values "
            "DROP COLUMN IF EXISTS source_record_key, "
            "DROP COLUMN IF EXISTS known_at"
        )
    )
