"""지역 특보 이벤트와 기존 snapshot projection의 배포 이력을 합친다."""

revision = "0037_release_merge"
down_revision = ("0036_snapshot_kind_projection", "0036_regional_alert_events")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
