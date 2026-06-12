"""fix_search_vector_restore_tsvector

Revision ID: c2860beb647d
Revises: e2ea8ac6e513
Create Date: 2026-06-13 00:15:25.062436

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2860beb647d'
down_revision: Union[str, Sequence[str], None] = 'e2ea8ac6e513'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Restore TSVECTOR on text_chunks.search_vector for BM25 full-text search."""
    # Step 1: Drop the broken TEXT column
    op.drop_column('text_chunks', 'search_vector')

    # Step 2: Re-add as TSVECTOR
    from sqlalchemy import Text, Computed
    from sqlalchemy.dialects.postgresql import TSVECTOR
    op.add_column('text_chunks',
        sa.Column('search_vector', TSVECTOR(),
                  sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
                  nullable=True))

    # Step 3: Create GIN index
    op.create_index('idx_text_chunks_search', 'text_chunks', ['search_vector'],
                    unique=False, postgresql_using='gin')


def downgrade() -> None:
    """Revert to plain TEXT."""
    op.drop_index('idx_text_chunks_search', table_name='text_chunks', postgresql_using='gin')
    op.drop_column('text_chunks', 'search_vector')
    op.add_column('text_chunks', sa.Column('search_vector', sa.Text(), nullable=True))
