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

## Why the backfill is one statement and not a loop of batches

Both batched forms were tried against production and both were far too slow, for
the same reason: batching makes the fact lookups *random*, and this database
sustains about 160 random reads a second.

"Update the next 50,000 rows that still disagree" is additionally quadratic --
each batch rescans from the start and re-joins every already-repaired row just
to learn it no longer qualifies -- but paging the primary key instead, which is
linear, still measured 160 rows/s and projected just under six hours, because
every row still costs one random read into the 33 GB fact table.

A single statement lets the planner hash the 3.7M pointers and read the fact
table sequentially, once.  That is the same total data and no random reads at
all.  Sequential throughput here is roughly two orders of magnitude better, so
the pass is minutes rather than hours.

The statement takes row locks, not table locks, so readers are unaffected --
but it holds those locks on every pointer row for the length of the pass, which
*would* stall a concurrently publishing older release.  ``deploy/n150.md`` says
to pause ingestion for this revision; that is why.

A pass that updates nothing has, by construction, just compared every pointer to
its fact and found them all consistent -- so it is the completion proof as well,
and no separate verification query is needed.  A second pass is expected to
repair nothing and simply confirm; more than that means rows are being made
stale faster than this repairs them, which is what ``_MAX_PASSES`` catches.

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

# One pass repairs the backlog; the second confirms it and picks up anything the
# outgoing release repointed while the first was running.  Needing more than a
# few means something is writing stale keys faster than we repair them.
_MAX_PASSES = 5
# The pass hashes the pointer table (3.7M rows, ~2 GB on production) and probes
# it with a sequential scan of the fact table.  The server default of 4 MB would
# spill that hash into hundreds of temp-file batches; this is a session-local
# setting on a one-shot migration container, not a server change.
_WORK_MEM = "256MB"
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

        op.execute(sa.text(f"SET work_mem = '{_WORK_MEM}'"))
        for _ in range(_MAX_PASSES):
            if _repair_pass(bind) == 0:
                return
        raise RuntimeError(
            f"current-value sort keys still disagreed with their facts after "
            f"{_MAX_PASSES} full passes; something is writing stale keys faster "
            "than this repairs them -- check that the previous release is no "
            "longer publishing before re-running"
        )


def _repair_pass(bind: sa.engine.Connection) -> int:
    """Compare every pointer to its fact in one statement, repairing as it goes.

    Returns the number of rows repaired.  Zero means this pass compared every
    pointer and found each already agreeing with its fact, which is the
    completion proof -- ``upgrade()`` needs no separate verification query.
    """
    return bind.execute(
        sa.text(
            "UPDATE weather_current_values cv "
            "SET known_at = wv.known_at, "
            "    source_record_key = wv.source_record_key "
            "FROM weather_values wv "
            "WHERE wv.value_id = cv.value_id "
            "  AND (cv.source_record_key IS DISTINCT FROM wv.source_record_key "
            "   OR cv.known_at IS DISTINCT FROM wv.known_at)"
        )
    ).rowcount


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
