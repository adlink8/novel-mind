"""25.2-03/25.3-04 skill_runtime 七表迁移、约束与级联权威测试。

覆盖:
  - 空库 upgrade heads → current 显示 27approval01
  - schema smoke：七张表 + 关键约束（ck_* / uq_* / idx_* / 循环外键）
  - downgrade 回 34readerbookmark → 再 re-upgrade 回 27approval01
  - 幂等：重复 upgrade 不报错（inspector 守卫）
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64

AGENT_TABLES = {
    "skill_registry",
    "skill_versions",
    "skill_runs",
    "artifacts",
    "artifact_revisions",
    "novel_agent_profiles",
    "approval_requests",
}


def test_heads_show_agent_runtime(empty_postgres: str, require_postgres: None):
    """upgrade to head 后 alembic heads 只显示一个 head 27approval01。"""
    run_alembic("upgrade", "head", database_url=empty_postgres)
    heads = run_alembic("heads", database_url=empty_postgres)
    head_lines = [
        line.strip()
        for line in (heads.stdout + heads.stderr).splitlines()
        if line.strip() and not line.strip().startswith("INFO")
    ]
    revision_tokens = [line.split()[0] for line in head_lines if line]
    assert len(revision_tokens) == 1
    assert revision_tokens[0] == "27approval01"


def test_six_tables_and_key_constraints(empty_postgres: str, require_postgres: None):
    """空库升级到 head：六张表存在，关键约束/外键齐全。"""
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
        assert AGENT_TABLES <= names

        # 循环外键已补上：artifacts.current_revision_id → artifact_revisions。
        artifacts_fks = {
            (fk.get("referred_table"), fk.get("constrained_columns") and fk["constrained_columns"][0])
            for fk in inspect(conn).get_foreign_keys("artifacts")
        }
        assert ("artifact_revisions", "current_revision_id") in artifacts_fks
        assert ("skill_versions", "skill_version_id") in artifacts_fks
        assert ("skill_runs", "run_id") in artifacts_fks

        # 不可变修订唯一键 + 自引用血缘外键。
        rev_uk = {
            tuple(u["column_names"])
            for u in inspect(conn).get_unique_constraints("artifact_revisions")
        }
        assert ("artifact_id", "revision_no") in rev_uk
        rev_fks = {
            fk.get("referred_table") for fk in inspect(conn).get_foreign_keys("artifact_revisions")
        }
        assert "artifact_revisions" in rev_fks  # parent_revision_id 自引用

        # 状态 CheckConstraint 存在。
        checks = {
            c["name"]
            for table in ("skill_runs", "artifacts", "skill_versions")
            for c in inspect(conn).get_check_constraints(table)
        }
        assert "ck_skill_runs_status" in checks
        assert "ck_artifacts_status" in checks
        assert "ck_skill_versions_yaml_checksum" in checks

        # novel_agent_profiles 唯一 scope。
        profile_uk = {
            tuple(u["column_names"])
            for u in inspect(conn).get_unique_constraints("novel_agent_profiles")
        }
        assert ("owner_id", "novel_id") in profile_uk
    engine.dispose()


def test_downgrade_then_reupgrade_cycle(empty_postgres: str, require_postgres: None):
    """downgrade 回 34readerbookmark → 表消失 → re-upgrade 回 head 正常。"""
    run_alembic("upgrade", "head", database_url=empty_postgres)

    down = run_alembic("downgrade", "34readerbookmark", database_url=empty_postgres)
    assert down.returncode == 0
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
        assert not (AGENT_TABLES & names)
    engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)
    current = run_alembic("current", database_url=empty_postgres)
    assert "27approval01" in current.stdout + current.stderr
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        assert AGENT_TABLES <= set(inspect(conn).get_table_names())
    engine.dispose()


def test_upgrade_is_idempotent(empty_postgres: str, require_postgres: None):
    """inspector 守卫：重复 upgrade 不产生错误。"""
    run_alembic("upgrade", "head", database_url=empty_postgres)
    again = run_alembic("upgrade", "head", database_url=empty_postgres)
    assert again.returncode == 0


def test_status_check_constraints_reject_invalid_values(
    empty_postgres: str, require_postgres: None
):
    """非法状态值被 ck_* 拒绝（数据库层 fail closed）。"""
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.begin() as conn:
        owner_id = conn.execute(
            text(
                """
                INSERT INTO users (username, email, hashed_password, is_active, is_superuser)
                VALUES ('agentuser1', 'agent1@example.com', 'x', true, false)
                RETURNING id
                """
            )
        ).scalar_one()
        novel_id = conn.execute(
            text(
                """
                INSERT INTO novels (title, author, owner_id, status, chapter_count, word_count)
                VALUES ('Agent Novel', 'Author', :owner_id, 'ready', 1, 10)
                RETURNING id
                """
            ),
            {"owner_id": owner_id},
        ).scalar_one()
        registry_id = conn.execute(
            text(
                """
                INSERT INTO skill_registry (owner_id, novel_id, name, status)
                VALUES (:owner, :novel, 'skill-a', 'active')
                RETURNING id
                """
            ),
            {"owner": owner_id, "novel": novel_id},
        ).scalar_one()

    # 每个被 ck_* 拒绝的插入放独立的 begin() 块，避免事务被污染
    # （PostgreSQL aborted transaction 会拒绝后续命令）。
    with engine.begin() as conn:
        # skill_runs 非法状态
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO skill_runs (
                        owner_id, novel_id, skill_version_id, status,
                        input_hash
                    ) VALUES (:owner, :novel, 1, 'evil', :h)
                    """
                ),
                {"owner": owner_id, "novel": novel_id, "h": HEX64},
            )

    with engine.begin() as conn:
        # skill_registry 非法状态
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO skill_registry (owner_id, novel_id, name, status)
                    VALUES (:owner, :novel, 'bad-skill', 'evil')
                    """
                ),
                {"owner": owner_id, "novel": novel_id},
            )

    with engine.begin() as conn:
        # skill_versions yaml_checksum 长度校验
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO skill_versions (
                        registry_id, owner_id, novel_id, name, version,
                        yaml_checksum
                    ) VALUES (:reg, :owner, :novel, 'skill-a', '1.0.0', 'tooshort')
                    """
                ),
                {"reg": registry_id, "owner": owner_id, "novel": novel_id},
            )
    engine.dispose()
