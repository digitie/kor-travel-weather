"""give ``weather_values`` a forward window of dated partitions, fresh DB included.

Revision 0015 created dated partitions only for the days the existing rows
covered.  On a fresh database there were none, so it created none, and the
nightly maintenance job was then the only thing that could.  It never did:
creating a partition while DEFAULT already held rows for that day is refused,
and checking means scanning DEFAULT under an ACCESS EXCLUSIVE lock.  Production
(fresh since 2026-09-20) ended up with every fact in DEFAULT -- 35M rows, 36 GB,
every index far larger than memory -- so each insert paid random reads, the KMA
and external runs crawled, and retention could drop nothing.

This revision closes the fresh-database half.  When DEFAULT is empty it adds
DEFAULT's floor (``CHECK (known_at < yesterday)``, validated -- instant on an
empty table) and creates the partitions from yesterday to a week ahead.  From
then on the maintenance job extends the window without ever scanning DEFAULT
(``kortravelweather.partitions.DEFAULT_FLOOR_CONSTRAINT``).

When DEFAULT already holds rows the revision does nothing: validating the floor
there is a full scan, and a deploy is not the place for it.
``scripts/weather_values_forward_partitions.py`` does that step, under an
operator's eye, while writers keep running.
"""

from datetime import timedelta

from alembic import op
from kortravelweather.models import kst_now
from kortravelweather.partitions import (
    DEFAULT_FLOOR_CONSTRAINT,
    DEFAULT_PARTITION,
    add_default_floor,
    default_partition_floor,
    default_partition_is_empty,
    ensure_default_partition,
    ensure_forward_partitions,
    kst_midnight,
    validate_default_floor,
)

revision = "0017_forward_partitions"
down_revision = "0016_purge_lookup_indexes"
branch_labels = None
depends_on = None

#: The same default as ``retention_ahead_days``; the nightly job takes over.
FORWARD_DAYS = 7


def upgrade() -> None:
    bind = op.get_bind()
    ensure_default_partition(bind)
    floor = default_partition_floor(bind)
    if floor is None:
        if not default_partition_is_empty(bind):
            print(
                f"0017: {DEFAULT_PARTITION} holds rows and has no floor; "
                "run scripts/weather_values_forward_partitions.py"
            )
            return
        add_default_floor(bind, kst_midnight(kst_now().date() - timedelta(days=1)))
        validate_default_floor(bind)
        floor = default_partition_floor(bind)
        assert floor is not None
    today = kst_now().date()
    ensure_forward_partitions(
        bind,
        floor=floor,
        start=today - timedelta(days=1),
        end=today + timedelta(days=FORWARD_DAYS),
    )


def downgrade() -> None:
    # The partitions stay: they hold data, and 0016's schema is fine with them.
    op.execute(
        f"ALTER TABLE {DEFAULT_PARTITION} DROP CONSTRAINT IF EXISTS {DEFAULT_FLOOR_CONSTRAINT}"
    )
