"""create_eval_tables

Revision ID: 518675fa18f8
Revises: c2860beb647d
Create Date: 2026-06-13 11:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '518675fa18f8'
down_revision: Union[str, Sequence[str], None] = 'c2860beb647d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create eval_datasets, eval_runs, eval_results tables."""
    # --- eval_datasets ---
    op.create_table(
        'eval_datasets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('novel_id', sa.Integer(), sa.ForeignKey('novels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('question_type', sa.String(50), nullable=False, server_default='original_text'),
        sa.Column('difficulty', sa.String(20), nullable=False, server_default='medium'),
        sa.Column('gold_chunks', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('expected_points', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('must_not_say', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('status', sa.String(20), nullable=False, server_default='candidate'),
        sa.Column('created_by', sa.String(50), nullable=True, server_default='auto'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_eval_datasets_novel_id', 'eval_datasets', ['novel_id'])
    op.create_index('idx_eval_datasets_status', 'eval_datasets', ['status'])

    # --- eval_runs ---
    op.create_table(
        'eval_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_name', sa.String(200), nullable=False),
        sa.Column('strategy', sa.String(50), nullable=False, server_default='hybrid_search'),
        sa.Column('novel_id', sa.Integer(), sa.ForeignKey('novels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('total_questions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('recall_at_k', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('precision_at_k', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('mrr', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('ndcg_at_k', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('faithfulness_score', sa.Float(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('config_snapshot', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_eval_runs_novel_id', 'eval_runs', ['novel_id'])
    op.create_index('idx_eval_runs_strategy', 'eval_runs', ['strategy'])

    # --- eval_results ---
    op.create_table(
        'eval_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('eval_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('dataset_id', sa.Integer(), sa.ForeignKey('eval_datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('recalled_chunks', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('answer_text', sa.Text(), nullable=True),
        sa.Column('score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('metrics', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('is_error_case', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_eval_results_run_id', 'eval_results', ['run_id'])
    op.create_index('idx_eval_results_dataset_id', 'eval_results', ['dataset_id'])


def downgrade() -> None:
    """Drop eval tables."""
    op.drop_table('eval_results')
    op.drop_table('eval_runs')
    op.drop_table('eval_datasets')
