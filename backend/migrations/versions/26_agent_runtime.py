"""Skill Runtime and Artifact Contract authority (25.2-03 / D-09..D-14).

六张表承载智能体运行时唯一事实源：
  skill_registry / skill_versions / skill_runs / artifacts /
  artifact_revisions / novel_agent_profiles

设计约定:
  - raw-SQL DDL（参考 17_narrative_memory_qualification.py）+ 幂等 inspector
    守卫（参考 24_chunk_index_journal.py）+ 对称 downgrade。
  - 每张权威表反规范化 owner_id / novel_id 外键（users.id / novels.id，
    ON DELETE CASCADE）；血缘敏感外键（skill_runs→skill_versions、
    artifacts→skill_versions/skill_runs）用 ON DELETE RESTRICT。
  - 校验和 String(64)、成本 Numeric(18,8)、状态 CheckConstraint 命名 ck_*。
  - artifacts.current_revision_id 与 artifact_revisions.artifact_id 互相引用，
    采用先建 artifacts（普通列）→ 建 artifact_revisions → ALTER TABLE 补外键。

Revision ID: 26agentrun01
Revises: 34readerbookmark
  注：计划文本写的是 24idxjournal1，但工作区实际迁移链在 24idxjournal1 之后
  还有 31canonspace01→32creative01→33creative01→34readerbookmark 四个迁移，
  `alembic heads` 唯一 head 是 34readerbookmark。若 down_revision 用
  24idxjournal1 会分裂出第二个 head，直接打破单 head 不变量（
  test_alembic_heads_single）。故取实际 head，保证 26agentrun01 成为新头。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "26agentrun01"
down_revision = "34readerbookmark"
branch_labels = None
depends_on = None

def _create_tables(bind) -> None:
    """一次性建六张表（互相引用的外键用 ALTER TABLE 后补）。"""
    bind.execute(
        sa.text(
            """
            CREATE TABLE skill_registry (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                name VARCHAR(120) NOT NULL,
                description TEXT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'draft',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_skill_registry_status CHECK (
                    status IN ('draft','active','deprecated')
                ),
                CONSTRAINT uq_skill_registry_scope_name UNIQUE (
                    owner_id, novel_id, name
                )
            );
            CREATE INDEX idx_skill_registry_scope
                ON skill_registry(owner_id, novel_id);

            CREATE TABLE skill_versions (
                id SERIAL PRIMARY KEY,
                registry_id INTEGER NOT NULL
                    REFERENCES skill_registry(id) ON DELETE CASCADE,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                name VARCHAR(120) NOT NULL,
                version VARCHAR(32) NOT NULL,
                description TEXT NULL,
                yaml_checksum VARCHAR(64) NOT NULL,
                allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
                read_permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                write_permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                forbidden_spaces JSONB NOT NULL DEFAULT '[]'::jsonb,
                budget JSONB NOT NULL DEFAULT '{}'::jsonb,
                approval_required_for JSONB NOT NULL DEFAULT '[]'::jsonb,
                input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
                output_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
                status VARCHAR(16) NOT NULL DEFAULT 'draft',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_skill_versions_status CHECK (
                    status IN ('draft','active','deprecated')
                ),
                CONSTRAINT ck_skill_versions_yaml_checksum CHECK (
                    length(yaml_checksum) = 64
                ),
                CONSTRAINT uq_skill_versions_registry_version UNIQUE (
                    registry_id, version
                )
            );
            CREATE INDEX idx_skill_versions_scope
                ON skill_versions(owner_id, novel_id, registry_id);

            CREATE TABLE skill_runs (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                skill_version_id INTEGER NOT NULL
                    REFERENCES skill_versions(id) ON DELETE RESTRICT,
                status VARCHAR(16) NOT NULL DEFAULT 'queued',
                status_reason VARCHAR(160) NULL,
                stop_reason VARCHAR(32) NULL,
                branch VARCHAR(80) NULL,
                input JSONB NOT NULL DEFAULT '{}'::jsonb,
                input_hash VARCHAR(64) NOT NULL,
                frozen_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
                model_lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
                source_versions JSONB NOT NULL DEFAULT '{}'::jsonb,
                budget_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                cost_usd NUMERIC(18, 8) NULL,
                internal_token_hash VARCHAR(64) NULL,
                cancel_requested BOOLEAN NOT NULL DEFAULT false,
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_code VARCHAR(80) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_skill_runs_status CHECK (
                    status IN ('queued','running','cancelled','completed','failed')
                ),
                CONSTRAINT ck_skill_runs_input_hash CHECK (length(input_hash) = 64),
                CONSTRAINT ck_skill_runs_retry_count CHECK (retry_count >= 0)
            );
            CREATE INDEX idx_skill_runs_scope
                ON skill_runs(owner_id, novel_id, skill_version_id);
            CREATE INDEX idx_skill_runs_status ON skill_runs(status);

            -- artifacts：current_revision_id 先用普通列，后补外键。
            CREATE TABLE artifacts (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                skill_version_id INTEGER NOT NULL
                    REFERENCES skill_versions(id) ON DELETE RESTRICT,
                run_id INTEGER NOT NULL
                    REFERENCES skill_runs(id) ON DELETE RESTRICT,
                branch VARCHAR(80) NULL,
                type VARCHAR(40) NOT NULL,
                schema_version VARCHAR(32) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'candidate',
                model_lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
                source_versions JSONB NOT NULL DEFAULT '{}'::jsonb,
                input_hash VARCHAR(64) NOT NULL,
                current_revision_id INTEGER NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_artifacts_status CHECK (
                    status IN ('candidate','validated','approved','published',
                               'rejected')
                ),
                CONSTRAINT ck_artifacts_input_hash CHECK (length(input_hash) = 64)
            );
            CREATE INDEX idx_artifacts_scope
                ON artifacts(owner_id, novel_id, run_id);
            CREATE INDEX idx_artifacts_status ON artifacts(status);

            CREATE TABLE artifact_revisions (
                id SERIAL PRIMARY KEY,
                artifact_id INTEGER NOT NULL
                    REFERENCES artifacts(id) ON DELETE CASCADE,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                revision_no INTEGER NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                parent_revision_id INTEGER NULL
                    REFERENCES artifact_revisions(id) ON DELETE SET NULL,
                evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                content JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_artifact_revisions_revision_no CHECK (revision_no >= 1),
                CONSTRAINT ck_artifact_revisions_content_hash CHECK (
                    length(content_hash) = 64
                ),
                CONSTRAINT uq_artifact_revisions_revision UNIQUE (
                    artifact_id, revision_no
                )
            );
            CREATE INDEX idx_artifact_revisions_artifact
                ON artifact_revisions(artifact_id, revision_no);

            -- 补 artifacts.current_revision_id 外键（循环引用）。
            ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts_current_revision
                FOREIGN KEY (current_revision_id)
                REFERENCES artifact_revisions(id) ON DELETE SET NULL;

            CREATE TABLE novel_agent_profiles (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                agent_profile_version VARCHAR(32) NOT NULL,
                enabled_skills JSONB NOT NULL DEFAULT '[]'::jsonb,
                world_model_version VARCHAR(64) NULL,
                narrative_memory_version VARCHAR(64) NULL,
                visual_bible_version VARCHAR(64) NULL,
                reading_cutoff INTEGER NULL,
                active_derivative_branch VARCHAR(80) NULL,
                recent_artifacts JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_novel_agent_profiles_scope UNIQUE (owner_id, novel_id)
            );
            CREATE INDEX idx_novel_agent_profiles_scope
                ON novel_agent_profiles(owner_id, novel_id);
            """
        )
    )


def upgrade() -> None:
    """Upgrade schema.

    幂等守卫：历史迁移（10/11/12 等）可能通过 ORM checkfirst 路径已建表；
    只在缺失时创建（与 24_chunk_index_journal 同一 inspector 模式）。
    """
    insp = sa.inspect(op.get_bind())
    if insp.has_table("skill_registry"):
        return
    _create_tables(op.get_bind())


def downgrade() -> None:
    """Downgrade schema：按依赖逆序删除并移除循环外键。"""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("skill_registry"):
        return
    bind = op.get_bind()
    # 逆序删除：先叶子表，再断循环外键，再删父表。
    if insp.has_table("novel_agent_profiles"):
        op.drop_table("novel_agent_profiles")
    # artifacts.current_revision_id 引用 artifact_revisions，必须先断该外键。
    if insp.has_table("artifacts"):
        bind.execute(
            sa.text(
                "ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS "
                "fk_artifacts_current_revision"
            )
        )
    if insp.has_table("artifact_revisions"):
        op.drop_table("artifact_revisions")
    if insp.has_table("artifacts"):
        op.drop_table("artifacts")
    for table in ("skill_runs", "skill_versions", "skill_registry"):
        if insp.has_table(table):
            op.drop_table(table)
