"""성공 승격 이력을 pending sweep의 반복 스캔에서 제외한다."""

import sqlalchemy as sa

from alembic import op

revision = "0032_export_publication_pending"
down_revision = "0031_external_export_cohorts"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    previous = dict(connection.execute(sa.text(
        "SELECT name, setting::bigint FROM pg_settings "
        "WHERE name IN ('statement_timeout', 'lock_timeout')"
    )).all())
    for name, maximum in (("statement_timeout", 60000), ("lock_timeout", 5000)):
        bounded = min(previous[name] or maximum, maximum)
        connection.execute(sa.text("SELECT set_config(:name, :value, true)"),
                           {"name": name, "value": f"{bounded}ms"})
    op.add_column("weather_export_attempts", sa.Column(
        "published_at", sa.DateTime(timezone=True), nullable=True,
    ))
    # 한 번의 운영 migration만 실제 성공 publication 시각으로 backfill한다.
    # timeout이면 DDL/행 수정/버전 모두 rollback하며 서비스 drain 뒤 재시도한다.
    op.execute(sa.text(
        "UPDATE weather_export_attempts AS a SET published_at = g.created_at "
        "FROM weather_export_generations AS g WHERE a.attempt_id = g.attempt_id"
    ))
    op.create_index(
        "ix_weather_export_attempts_pending", "weather_export_attempts", ["attempt_id"],
        postgresql_where=sa.text(
            "status IN ('preparing', 'sealed') AND published_at IS NULL"
        ),
    )
    for name, value in previous.items():
        connection.execute(sa.text("SELECT set_config(:name, :value, true)"),
                           {"name": name, "value": f"{value}ms"})


def downgrade():
    op.drop_index("ix_weather_export_attempts_pending", table_name="weather_export_attempts")
    op.drop_column("weather_export_attempts", "published_at")
