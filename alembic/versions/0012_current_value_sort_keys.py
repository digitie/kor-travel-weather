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
lock it takes is held for milliseconds and no rewrite happens.  *Waiting* for
that lock is the unbounded part -- one open transaction on the table is enough
to queue it, and every reader arriving afterwards queues behind it -- so the
statements run under a short ``lock_timeout`` and retry rather than stalling the
API.  The backfill that follows takes only row locks and does not block
readers.  Doing both in one statement would instead hold ``ACCESS EXCLUSIVE``
across a sequential scan of the 28 GB fact table, and every reader -- and the
whole public API, which waits on this migration -- would block for the
duration.

The backfill repairs rows whose keys **disagree with the fact the pointer
names**, not rows whose keys are missing.  That distinction matters: the
outgoing release moves an existing pointer by writing ``value_id`` alone,
because the new columns are not in its model.  Such a row ends up with a new
``value_id`` and the previous fact's sort keys -- stale, not null -- and a
"backfill what is missing" pass would walk past it forever.  The column adds
use ``IF NOT EXISTS`` for the same resumability reason.  Alembic writes
``alembic_version`` only after ``upgrade()`` returns, so anything committed
before that point has to be safe to repeat.

## Why the batches are a keyset sweep

The obvious batching -- "update the next 50,000 rows that still disagree" --
is quadratic, and only on a table large enough to matter.  Each batch rescans
from the beginning and joins every already-repaired row to the fact table again
just to discover it no longer qualifies, so batch *n* does *n* times the work of
batch 1.  Measured on production (3.75M pointers over a 33 GB fact table): about
70 s for the first batch and rising, against 75 batches.

So the batches walk the primary key instead, each one starting where the last
ended.  Every row is visited exactly once per sweep and the disagreement test is
a filter rather than the thing being searched for, which makes a sweep linear.

A sweep that updates nothing has, by construction, just visited every row and
found them all consistent -- so it is the completion proof as well, and no
separate verification pass is needed.  More than one sweep is expected while the
previous release is still repointing rows behind us; a repeat that keeps finding
work is the signal something is wrong, and that is what ``_MAX_SWEEPS`` catches.

The columns stay nullable.  Compose starts this migration while the previous
release is still serving and ingesting, and that release writes pointer rows
without the new columns; a ``NOT NULL`` here would fail those inserts and break
collection mid-deploy.  Rows that release *inserts* during the window carry null
keys and sort last within their group (``nullslast``); rows it *repoints* carry
stale ones.  Both are corrected the next time this migration's backfill runs, or
when the logical point receives another revision under the new code.

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
# 3,746,027 rows on production at the time of writing.  A sweep visits each row
# once, so this cap only exists so a pathological loop cannot run forever.
_MAX_BATCHES = 10_000
# One sweep repairs the backlog; the second exists to confirm it and to catch
# rows the outgoing release repointed while the first was running.  Needing more
# than a few means something is writing stale keys faster than we fix them.
_MAX_SWEEPS = 5
_LOCK_TIMEOUT = "2s"
_LOCK_ATTEMPTS = 30


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        # The lock is held for milliseconds, but waiting for it is unbounded:
        # one open transaction on the table puts ADD COLUMN in the queue, and
        # every reader that arrives after it queues behind that.  Give up
        # quickly and retry instead of stalling the whole API behind a Dagster
        # publish.  Retrying is free because the statements are idempotent.
        for attempt in range(_LOCK_ATTEMPTS):
            try:
                op.execute(sa.text(f"SET lock_timeout = '{_LOCK_TIMEOUT}'"))
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
                break
            except sa.exc.OperationalError:
                if attempt == _LOCK_ATTEMPTS - 1:
                    raise
        op.execute(sa.text("SET lock_timeout = 0"))

        for _ in range(_MAX_SWEEPS):
            if _sweep(bind) == 0:
                return
        raise RuntimeError(
            f"current-value sort keys still disagreed with their facts after "
            f"{_MAX_SWEEPS} full sweeps; something is writing stale keys faster "
            "than this repairs them -- check that the previous release is no "
            "longer publishing before re-running"
        )


def _sweep(bind: sa.engine.Connection) -> int:
    """Walk the whole table once by primary key, repairing as we go.

    Returns the number of rows repaired.  Zero means this sweep looked at every
    pointer and found each one already agreeing with its fact, which is the
    completion proof -- ``upgrade()`` needs no separate verification query.
    """
    cursor = ""
    repaired = 0
    for _ in range(_MAX_BATCHES):
        last, seen, updated = bind.execute(
            sa.text(
                "WITH batch AS ("
                "  SELECT value_id FROM weather_current_values "
                "  WHERE value_id > :cursor ORDER BY value_id LIMIT :batch"
                "), repaired AS ("
                "  UPDATE weather_current_values cv "
                "  SET known_at = wv.known_at, "
                "      source_record_key = wv.source_record_key "
                "  FROM weather_values wv "
                "  WHERE cv.value_id IN (SELECT value_id FROM batch) "
                "    AND wv.value_id = cv.value_id "
                "    AND (cv.source_record_key IS DISTINCT FROM "
                "         wv.source_record_key "
                "     OR cv.known_at IS DISTINCT FROM wv.known_at) "
                "  RETURNING 1"
                ") SELECT (SELECT max(value_id) FROM batch), "
                "         (SELECT count(*) FROM batch), "
                "         (SELECT count(*) FROM repaired)"
            ),
            {"cursor": cursor, "batch": _BATCH_ROWS},
        ).one()
        if seen == 0:
            return repaired
        cursor = last
        repaired += updated
    raise RuntimeError(
        f"the backfill sweep did not reach the end of weather_current_values in "
        f"{_MAX_BATCHES} batches of {_BATCH_ROWS}; raise _MAX_BATCHES"
    )


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
