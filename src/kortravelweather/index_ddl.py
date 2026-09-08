"""Concurrent partial-index DDL shared by the marker index migrations.

``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` accepts any index that merely
shares the name.  Two schema paths claim these names -- ``Base.metadata
.create_all`` and the migration chain -- so an index one path built with a
different definition is otherwise permanent, and a failed concurrent build
leaves an unusable index that ``IF NOT EXISTS`` then skips forever.

Only the mechanism lives here.  Each migration keeps its own frozen copy of the
index definition it intends.
"""

from __future__ import annotations

import re

import sqlalchemy as sa


def normalize_index_definition(definition: str) -> str:
    """Reduce a stored or intended definition to a comparable form."""
    collapsed = re.sub(r"\s+", " ", definition).strip().rstrip(";")
    collapsed = collapsed.replace("public.", "").replace("USING btree ", "")
    # PostgreSQL stores predicates fully parenthesised and casts varchar
    # comparisons; drop the noise that never carries meaning here.
    collapsed = collapsed.replace("(", " ").replace(")", " ")
    collapsed = re.sub(r"::[a-z ]+(\[\])?", "", collapsed)
    collapsed = re.sub(r"\s+", " ", collapsed)
    return collapsed.strip().lower()


def index_is_invalid(bind: sa.engine.Connection, name: str) -> bool:
    """True when a previous CONCURRENTLY build left the index unusable."""
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


def stored_index_definition(bind: sa.engine.Connection, name: str) -> str | None:
    return bind.execute(
        sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": name},
    ).scalar()


def ensure_concurrent_index(
    execute,
    bind: sa.engine.Connection,
    *,
    name: str,
    table: str,
    columns: str,
    where: str,
) -> None:
    """Create ``name``, rebuilding it when what is stored differs.

    ``execute`` is the migration's ``op.execute``.  ``columns`` and ``where``
    are raw SQL so a migration can state exactly the definition it intends and
    compare against what the database actually holds.
    """
    intended = f"CREATE INDEX {name} ON {table} {columns} WHERE ({where})"
    stored = stored_index_definition(bind, name)
    if stored is not None:
        if not index_is_invalid(bind, name) and normalize_index_definition(
            stored
        ) == normalize_index_definition(intended):
            return
        execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
    execute(
        sa.text(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} "
            f"{columns} WHERE {where}"
        )
    )
