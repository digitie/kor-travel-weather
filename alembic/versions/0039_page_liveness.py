"""완료 페이지 참조 대상의 트랜잭션 삭제 epoch를 추가한다."""

from alembic import op
from kortravelweather.export_page_liveness import install_page_liveness, remove_page_liveness

revision = "0039_page_liveness"
down_revision = "0038_snapshot_selection"
branch_labels = None
depends_on = None


def upgrade():
    install_page_liveness(op.get_bind())


def downgrade():
    remove_page_liveness(op.get_bind())
