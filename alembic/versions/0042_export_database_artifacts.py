"""불변 export chunk를 fact 보존과 독립된 DB read model에 보관한다."""

import sqlalchemy as sa

from alembic import op

revision = "0042_export_database_artifacts"
down_revision = "0041_export_receipt_gc_index"
branch_labels = None
depends_on = None

TABLE = "weather_export_artifacts"
INDEX = "ix_weather_export_artifacts_created_digest"
TRIGGER = "trg_weather_export_artifacts_immutable"
FUNCTION = "weather_export_artifacts_reject_update"


def upgrade():
    op.create_table(
        TABLE,
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_count BETWEEN 0 AND 8388608", name="ck_weather_artifact_bytes"),
        sa.CheckConstraint("point_count BETWEEN 0 AND 5000", name="ck_weather_artifact_points"),
    )
    # digest 단독은 PK 색인이 맡고, GC는 생성시각/동일시각 digest keyset을 쓴다.
    op.create_index(INDEX, TABLE, ["created_at", "digest"])
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.create_check_constraint(
            "ck_weather_artifact_payload_bytes", TABLE, "octet_length(payload) = byte_count",
        )
        op.create_check_constraint(
            "ck_weather_artifact_payload_digest", TABLE, "encode(sha256(payload), 'hex') = digest",
        )
        op.execute(f"""
            CREATE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'weather export artifacts are immutable' USING ERRCODE='23514';
            END;
            $$
        """)
        op.execute(
            f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()"
        )
    elif dialect == "sqlite":
        op.execute(
            f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'weather export artifacts are immutable'); END"
        )
    else:
        raise RuntimeError("export artifact 불변 trigger를 지원하지 않는 DB입니다.")


def downgrade():
    op.drop_table(TABLE)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP FUNCTION {FUNCTION}()")
