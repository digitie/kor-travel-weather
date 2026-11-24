"""Map 표준 프로필 export 완료 페이지의 사전 구성 표(호출 검토 A3).

두 표 모두 새로 만들며 기존 행은 건드리지 않는다. build 행은 기존 snapshot handle을 FK로
가리키므로 ``weather_export_snapshots``에 짧은 SHARE ROW EXCLUSIVE lock이 필요하다. 그 대기는
migration 연결의 기본 ``lock_timeout``(5초, ``kortravelweather.migration_limits``)이 묶는다.
빈 새 표의 색인이라 ``CONCURRENTLY``가 필요 없다.
"""

import sqlalchemy as sa

from alembic import op

revision = "0044_export_prebuilt_pages"
down_revision = "0043_alert_success_index"
branch_labels = None
depends_on = None

BUILDS = "weather_export_prebuilt_builds"
PAGES = "weather_export_prebuilt_pages"
TRIGGER = "trg_weather_export_prebuilt_pages_immutable"
FUNCTION = "weather_export_prebuilt_pages_reject_update"


def upgrade():
    op.create_table(
        BUILDS,
        sa.Column("build_id", sa.String(36), primary_key=True),
        sa.Column("profile_key", sa.String(64), nullable=False),
        sa.Column(
            "snapshot_id", sa.String(36),
            sa.ForeignKey("weather_export_snapshots.snapshot_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("page_limit", sa.Integer(), nullable=False),
        sa.Column("proof_digest", sa.String(64), nullable=False),
        sa.Column("liveness_plans", sa.BigInteger(), nullable=False),
        sa.Column("liveness_generations", sa.BigInteger(), nullable=False),
        sa.Column("liveness_heads", sa.BigInteger(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_stale", sa.Boolean(), nullable=False),
        sa.Column("status_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("page_limit BETWEEN 1 AND 5000", name="ck_weather_prebuilt_limit"),
        sa.CheckConstraint("page_count BETWEEN 1 AND 64", name="ck_weather_prebuilt_pages"),
        sa.CheckConstraint(
            "byte_count BETWEEN 0 AND 67108864", name="ck_weather_prebuilt_bytes",
        ),
    )
    op.create_index(
        "ix_weather_export_prebuilt_builds_snapshot_id", BUILDS, ["snapshot_id"],
    )
    op.create_index(
        "ix_weather_export_prebuilt_builds_profile", BUILDS,
        ["profile_key", sa.text("status_checked_at DESC")],
    )
    op.create_index(
        "ix_weather_export_prebuilt_builds_manifest", BUILDS, ["manifest_digest", "page_limit"],
    )
    op.create_table(
        PAGES,
        sa.Column(
            "build_id", sa.String(36),
            sa.ForeignKey(f"{BUILDS}.build_id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("start_g", sa.Integer(), primary_key=True),
        sa.Column("start_p", sa.Integer(), primary_key=True),
        sa.Column("end_g", sa.Integer(), nullable=False),
        sa.Column("end_p", sa.Integer(), nullable=False),
        sa.Column("has_more", sa.Boolean(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "item_count BETWEEN 0 AND 5000", name="ck_weather_prebuilt_page_items",
        ),
        sa.CheckConstraint(
            "byte_count BETWEEN 0 AND 4194304", name="ck_weather_prebuilt_page_bytes",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        # 읽기 경로도 sha256을 다시 검산하지만, 저장 시점에 잘못된 본문을 받지 않는다.
        op.create_check_constraint(
            "ck_weather_prebuilt_page_payload_bytes", PAGES, "octet_length(payload) = byte_count",
        )
        op.create_check_constraint(
            "ck_weather_prebuilt_page_payload_digest", PAGES,
            "encode(sha256(payload), 'hex') = payload_digest",
        )
        op.execute(f"""
            CREATE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'weather export prebuilt pages are immutable' USING ERRCODE='23514';
            END;
            $$
        """)
        op.execute(
            f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {PAGES} "
            f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()"
        )


def downgrade():
    op.drop_table(PAGES)
    op.drop_table(BUILDS)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP FUNCTION {FUNCTION}()")
