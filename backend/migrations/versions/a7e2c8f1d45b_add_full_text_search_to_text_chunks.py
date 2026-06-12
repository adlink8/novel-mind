"""add full-text search vector to text_chunks

Revision ID: a7e2c8f1d45b
Revises: f3c8b7b2dbf7
Create Date: 2026-06-12 20:00:00.000000

为 text_chunks 表添加 PostgreSQL 全文搜索支持：
1. 新增 search_vector tsvector 列
2. 创建 GIN 索引加速全文检索
3. 创建触发器自动更新 search_vector（insert/update 时从 content 生成）
使用 pg_catalog.simple 分词器，按字切分中文文本。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7e2c8f1d45b'
down_revision: Union[str, Sequence[str], None] = 'f3c8b7b2dbf7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """添加全文搜索支持"""
    # 1. 添加 search_vector tsvector 列
    op.add_column(
        'text_chunks',
        sa.Column(
            'search_vector',
            sa.dialects.postgresql.TSVECTOR(),
            nullable=True,
        ),
    )

    # 2. 对已有数据填充 search_vector
    op.execute(
        sa.text(
            "UPDATE text_chunks SET search_vector = to_tsvector('pg_catalog.simple', content)"
        )
    )

    # 3. 创建 GIN 索引
    op.create_index(
        'idx_text_chunks_search',
        'text_chunks',
        ['search_vector'],
        unique=False,
        postgresql_using='GIN',
    )

    # 4. 创建触发器函数（如果不存在）
    op.execute(
        sa.text("""
            CREATE OR REPLACE FUNCTION text_chunks_search_vector_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector := to_tsvector('pg_catalog.simple', NEW.content);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
    )

    # 5. 创建触发器
    op.execute(
        sa.text("""
            CREATE TRIGGER tsvector_update
            BEFORE INSERT OR UPDATE OF content
            ON text_chunks
            FOR EACH ROW
            EXECUTE FUNCTION text_chunks_search_vector_trigger();
        """)
    )


def downgrade() -> None:
    """移除全文搜索支持"""
    # 1. 删除触发器
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS tsvector_update ON text_chunks")
    )

    # 2. 删除触发器函数
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS text_chunks_search_vector_trigger()")
    )

    # 3. 删除 GIN 索引
    op.drop_index(
        'idx_text_chunks_search',
        table_name='text_chunks',
        if_exists=True,
    )

    # 4. 删除 search_vector 列
    op.drop_column('text_chunks', 'search_vector')
