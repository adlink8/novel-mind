"""ApprovalRequest authority (25.3-04 / D-11 / D-15 / REQ-AGENT-07).

第七张权威表 approval_requests：承载 Web 审批请求的唯一事实源。
设计约定（延续 26_agent_runtime.py）:
  - raw-SQL DDL + 幂等 inspector 守卫 + 对称 downgrade。
  - 反规范化 owner_id（users.id，ON DELETE CASCADE）；血缘敏感外键
    （run/skill_version/artifact/revision）用 ON DELETE SET NULL——
    审批记录不阻塞血缘清理。
  - 状态 CheckConstraint ck_approval_requests_status 覆盖
    APPROVAL_REQUEST_STATUSES；payload_hash 可空（CHECK 允许 NULL）。
  - no relationship()（house 约定）。

Revision ID: 27approval01
Revises: 26agentrun01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "27approval01"
down_revision = "26agentrun01"
branch_labels = None
depends_on = None


def _create_table(bind) -> None:
    bind.execute(
        sa.text(
            """
            CREATE TABLE approval_requests (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                run_id INTEGER NULL REFERENCES skill_runs(id) ON DELETE SET NULL,
                skill_version_id INTEGER NULL
                    REFERENCES skill_versions(id) ON DELETE SET NULL,
                artifact_id INTEGER NULL REFERENCES artifacts(id) ON DELETE SET NULL,
                artifact_revision_id INTEGER NULL
                    REFERENCES artifact_revisions(id) ON DELETE SET NULL,
                novel_id INTEGER NULL REFERENCES novels(id) ON DELETE CASCADE,
                branch_id INTEGER NULL,
                fork_id INTEGER NULL,
                action VARCHAR(120) NOT NULL,
                payload_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
                payload_hash VARCHAR(64) NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                decision_actor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                decided_at TIMESTAMPTZ NULL,
                expires_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_approval_requests_status CHECK (
                    status IN ('pending','approved','approved_for_session',
                               'rejected','expired','cancelled')
                ),
                CONSTRAINT ck_approval_requests_payload_hash CHECK (
                    payload_hash IS NULL OR length(payload_hash) = 64
                )
            );
            CREATE INDEX idx_approval_requests_scope
                ON approval_requests(owner_id, run_id);
            CREATE INDEX idx_approval_requests_status ON approval_requests(status);
            """
        )
    )


def upgrade() -> None:
    """Upgrade schema（幂等 inspector 守卫：表已存在则跳过）。"""
    insp = sa.inspect(op.get_bind())
    if insp.has_table("approval_requests"):
        return
    _create_table(op.get_bind())


def downgrade() -> None:
    """Downgrade schema：对称删除。"""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("approval_requests"):
        return
    op.drop_table("approval_requests")
