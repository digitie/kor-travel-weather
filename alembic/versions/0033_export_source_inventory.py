"""quota 선택 밖 위치도 유지하는 작은 위치별 승인 source inventory."""

from contextlib import contextmanager

import sqlalchemy as sa

from alembic import op

revision = "0033_export_source_inventory"
down_revision = "0032_export_publication_pending"
branch_labels = None
depends_on = None


@contextmanager
def bounded_ddl():
    connection = op.get_bind()
    previous = dict(connection.execute(sa.text(
        "SELECT name, setting::bigint FROM pg_settings "
        "WHERE name IN ('statement_timeout', 'lock_timeout')"
    )).all())
    for name, maximum in (("statement_timeout", 60000), ("lock_timeout", 5000)):
        bounded = min(previous[name] or maximum, maximum)
        connection.execute(sa.text("SELECT set_config(:name, :value, true)"),
                           {"name": name, "value": f"{bounded}ms"})
    yield
    # 실패 시 Alembic transaction rollback이 LOCAL 설정/DDL/버전을 함께 되돌린다.
    for name, value in previous.items():
        connection.execute(sa.text("SELECT set_config(:name, :value, true)"),
                           {"name": name, "value": f"{value}ms"})


def upgrade():
    with bounded_ddl():
        upgrade_schema()


def upgrade_schema():
    op.add_column("weather_export_snapshots", sa.Column(
        "issue_order", sa.BigInteger(), sa.Identity(), nullable=False,
    ))
    # 기존 handle은 이전(created_at,snapshot_id) 순서를 한 번만 고정한다.
    # 이후 발급은 DB identity sequence를 사용하며 OS 시계 역행과 분리한다.
    op.execute(sa.text(
        "WITH ranked AS (SELECT snapshot_id, row_number() OVER "
        "(ORDER BY created_at,snapshot_id) AS n FROM weather_export_snapshots) "
        "UPDATE weather_export_snapshots AS s SET issue_order=r.n "
        "FROM ranked AS r WHERE s.snapshot_id=r.snapshot_id"
    ))
    op.execute(sa.text(
        "SELECT setval(pg_get_serial_sequence('weather_export_snapshots','issue_order'), "
        "COALESCE(max(issue_order),1),count(*)>0) FROM weather_export_snapshots"
    ))
    op.create_index(
        "ix_weather_export_snapshots_issue_order", "weather_export_snapshots", ["issue_order"],
    )
    op.add_column("weather_export_external_cohorts", sa.Column("descriptors", sa.JSON()))
    op.create_table(
        "weather_export_approved_locations",
        sa.Column("provider", sa.String(120), primary_key=True),
        sa.Column("dataset_key", sa.String(160), primary_key=True),
        sa.Column("location_id", sa.String(120), primary_key=True),
        sa.Column("source_part", sa.String(16), primary_key=True, server_default="main"),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(36), sa.ForeignKey(
            "weather_export_generations.generation_id", ondelete="RESTRICT",
        ), nullable=False),
        sa.Column("source_scope", sa.String(64), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("nx", sa.Integer()), sa.Column("ny", sa.Integer()),
        sa.Column("source_collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_part IN ('main','land','temperature')", name="ck_weather_export_inventory_part",
        ),
    )
    op.create_index(
        "ix_weather_export_approved_locations_namespace", "weather_export_approved_locations",
        ["namespace"],
    )
    op.create_index(
        "ix_weather_export_approved_locations_generation_id", "weather_export_approved_locations",
        ["generation_id"],
    )
    op.create_index(
        "ix_weather_export_heads_generation_id", "weather_export_heads", ["generation_id"],
    )


def downgrade():
    with bounded_ddl():
        op.drop_index("ix_weather_export_heads_generation_id", table_name="weather_export_heads")
        op.drop_table("weather_export_approved_locations")
        op.drop_column("weather_export_external_cohorts", "descriptors")
        op.drop_column("weather_export_snapshots", "issue_order")
