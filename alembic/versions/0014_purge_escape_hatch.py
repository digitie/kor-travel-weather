"""let retention delete expired history without opening the immutable tables.

``weather_values`` and ``weather_source_records`` carry a ``BEFORE UPDATE OR
DELETE`` row trigger that refuses unconditionally, which is why history can be
trusted: a published fact is never quietly rewritten.  It also means nothing can
age history out, and the tables only grow.  On n150 that reached 23.3M facts and
33 GB, at which point the read path was doing 160 random reads a second against
a disk that had nothing left to give.

Retention is a different operation from mutation.  It removes whole rows that
are past the window and never alters a row that stays, so the guarantee worth
keeping is "no row is ever changed", not "no row is ever removed".

Rather than dropping the trigger for the duration of a purge -- which would open
UPDATE as well, for every session, for as long as the purge runs -- the function
now allows DELETE for a transaction that has explicitly asked for it:

    SET LOCAL kortravelweather.purge = 'on'

``SET LOCAL`` scopes it to that transaction, so a rollback or an error takes the
permission with it, and a session that never sets it sees the old behaviour
exactly.  UPDATE stays refused in every case.  The setting is read with
``current_setting(..., true)``, whose second argument returns NULL rather than
raising when the setting was never defined -- so an ordinary ingest transaction,
which is every transaction but the purge's, falls straight through to the
exception.
"""

import sqlalchemy as sa

from alembic import op
from kortravelweather.repository import IMMUTABLE_ROW_FUNCTION_SQL

revision = "0014_purge_escape_hatch"
down_revision = "0013_current_value_sort_index"
branch_labels = None
depends_on = None

_PREVIOUS_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION weather_immutable_row() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
END; $$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Imported rather than repeated: create_all and the migration chain must
    # install the same function body, and a test compares the two schemas.
    op.execute(sa.text(IMMUTABLE_ROW_FUNCTION_SQL))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(sa.text(_PREVIOUS_FUNCTION_SQL))
