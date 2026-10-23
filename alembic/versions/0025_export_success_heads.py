"""성공 generation/head와 원천 계획. 기존 이력 backfill/collector 활성화 없음."""
from alembic import op

revision = "0025_export_success_heads"
down_revision = "0024_export_plan_barrier"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE weather_export_scope_plans (
        attempt_id VARCHAR(36) NOT NULL,
        source_scope VARCHAR(64) NOT NULL,
        namespace VARCHAR(64) NOT NULL,
        descriptor JSON NOT NULL,
        bound_at TIMESTAMP WITH TIME ZONE NOT NULL,
        data_revision_at TIMESTAMP WITH TIME ZONE,
        source_input_digest VARCHAR(64),
        source_collected_at TIMESTAMP WITH TIME ZONE,
        expected_point_count INTEGER,
        normalization_digest VARCHAR(64),
        normalization_evidence JSON,
        PRIMARY KEY (attempt_id)
)
""")
    op.execute("""
CREATE TABLE weather_export_generations (
        generation_id VARCHAR(36) NOT NULL,
        attempt_id VARCHAR(36) NOT NULL,
        owner_id VARCHAR(36) NOT NULL,
        seal_digest VARCHAR(64) NOT NULL,
        namespace VARCHAR(64) NOT NULL,
        parent_generation_id VARCHAR(36),
        data_revision_at TIMESTAMP WITH TIME ZONE NOT NULL,
        source_collected_at TIMESTAMP WITH TIME ZONE NOT NULL,
        content_digest VARCHAR(64) NOT NULL,
        point_count INTEGER NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (generation_id),
        CONSTRAINT ck_weather_generation_nonempty CHECK (point_count > 0),
        UNIQUE (attempt_id)
)
""")
    op.execute("""CREATE INDEX ix_weather_export_generations_namespace ON weather_export_generations
(namespace)""")
    op.execute("""
CREATE TABLE weather_export_heads (
        namespace VARCHAR(64) NOT NULL,
        source_scope VARCHAR(64) NOT NULL,
        generation_id VARCHAR(36) NOT NULL,
        version BIGINT NOT NULL,
        data_revision_at TIMESTAMP WITH TIME ZONE NOT NULL,
        content_digest VARCHAR(64) NOT NULL,
        source_collected_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (namespace),
        CONSTRAINT ck_weather_head_version CHECK (version > 0),
        FOREIGN KEY(generation_id) REFERENCES weather_export_generations (generation_id)
)
""")
    op.execute("""CREATE INDEX ix_weather_export_heads_source_scope
ON weather_export_heads (source_scope)""")
    op.execute("""
CREATE TABLE weather_export_generation_chunks (
        generation_id VARCHAR(36) NOT NULL,
        ordinal INTEGER NOT NULL,
        artifact_digest VARCHAR(64) NOT NULL,
        byte_count INTEGER NOT NULL,
        point_count INTEGER NOT NULL,
        PRIMARY KEY (generation_id, ordinal),
        CONSTRAINT uq_weather_generation_artifact UNIQUE (generation_id,
artifact_digest),
        FOREIGN KEY(generation_id) REFERENCES weather_export_generations (generation_id)
)
""")
    op.execute("""CREATE INDEX ix_weather_export_generation_chunks_artifact_digest ON
weather_export_generation_chunks (artifact_digest)""")
    op.execute("""
CREATE TABLE weather_export_pending_refs (
        attempt_id VARCHAR(36) NOT NULL,
        chunk_key VARCHAR(64) NOT NULL,
        artifact_digest VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (attempt_id, chunk_key)
)
""")
    op.execute("""CREATE INDEX ix_weather_export_pending_refs_artifact_digest ON
weather_export_pending_refs (artifact_digest)""")


def downgrade():
    op.drop_table('weather_export_pending_refs')
    op.drop_table('weather_export_generation_chunks')
    op.drop_table('weather_export_heads')
    op.drop_table('weather_export_generations')
    op.drop_table('weather_export_scope_plans')
