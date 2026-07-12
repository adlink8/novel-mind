"""
评测 ORM 模型测试

验证 EvalDataset、EvalRun、EvalResult 的创建、查询、级联删除和序列化。
"""
import pytest

pytestmark = pytest.mark.unit
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import EvalDataset, EvalRun, EvalResult
from app.models.novel import Novel
from app.models.user import User


async def _create_user_and_novel(db_session: AsyncSession, username: str):
    """Helper: 创建测试用户和小说，返回 (user, novel)"""
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()

    novel = Novel(title=f"测试小说_{username}", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()
    return user, novel


@pytest.mark.asyncio
async def test_create_eval_dataset(db_session: AsyncSession):
    """测试创建评测测试题"""
    _, novel = await _create_user_and_novel(db_session, "evaltest")

    dataset = EvalDataset(
        novel_id=novel.id,
        question="路明非在卡塞尔学院的第一次任务是什么？",
        question_type="original_text",
        difficulty="medium",
        gold_chunks=[1, 3, 7],
        expected_points=["前往三峡水库", "执行夔门计划", "遭遇龙族初代种"],
        must_not_say=["路明非一开始就成功了"],
        status="candidate",
        created_by="auto",
    )
    db_session.add(dataset)
    await db_session.commit()

    result = await db_session.get(EvalDataset, dataset.id)
    assert result is not None
    assert result.novel_id == novel.id
    assert result.question_type == "original_text"
    assert result.difficulty == "medium"
    assert 1 in result.gold_chunks
    assert "前往三峡水库" in result.expected_points
    assert "路明非一开始就成功了" in result.must_not_say
    assert result.status == "candidate"


@pytest.mark.asyncio
async def test_create_eval_run(db_session: AsyncSession):
    """测试创建评测运行"""
    _, novel = await _create_user_and_novel(db_session, "run_test")

    run = EvalRun(
        run_name="baseline vs hybrid 对比测试",
        strategy="hybrid_search",
        novel_id=novel.id,
        total_questions=50,
        recall_at_k=0.72,
        precision_at_k=0.65,
        mrr=0.58,
        ndcg_at_k=0.71,
        latency_ms=320.5,
        config_snapshot={"k": 5, "weight_bm25": 0.5, "weight_vector": 0.5},
    )
    db_session.add(run)
    await db_session.commit()

    result = await db_session.get(EvalRun, run.id)
    assert result is not None
    assert result.run_name == "baseline vs hybrid 对比测试"
    assert result.strategy == "hybrid_search"
    assert result.recall_at_k == 0.72
    assert result.latency_ms == 320.5
    assert result.config_snapshot["k"] == 5


@pytest.mark.asyncio
async def test_create_eval_result(db_session: AsyncSession):
    """测试创建评测结果"""
    _, novel = await _create_user_and_novel(db_session, "result_test")

    dataset = EvalDataset(
        novel_id=novel.id,
        question="测试问题",
        question_type="original_text",
        gold_chunks=[5],
        expected_points=[],
        must_not_say=[],
    )
    run = EvalRun(
        run_name="单题测试",
        strategy="baseline_vector",
        novel_id=novel.id,
        config_snapshot={},
    )
    db_session.add_all([dataset, run])
    await db_session.flush()

    er = EvalResult(
        run_id=run.id,
        dataset_id=dataset.id,
        recalled_chunks=[5, 12, 3],
        answer_text="测试答案",
        score=0.33,
        metrics={"recall@5": 0.33, "precision@5": 0.2, "mrr": 1.0},
        is_error_case=False,
    )
    db_session.add(er)
    await db_session.commit()

    result = await db_session.get(EvalResult, er.id)
    assert result is not None
    assert result.recalled_chunks == [5, 12, 3]
    assert result.score == 0.33
    assert result.is_error_case is False


@pytest.mark.asyncio
async def test_eval_cascade_delete_novel(db_session: AsyncSession):
    """测试级联删除：删除 Novel 时级联删除关联的评测数据"""
    _, novel = await _create_user_and_novel(db_session, "cascade_test")

    dataset = EvalDataset(
        novel_id=novel.id,
        question="级联测试题",
        question_type="original_text",
        gold_chunks=[],
        expected_points=[],
        must_not_say=[],
    )
    run = EvalRun(
        run_name="级联运行",
        strategy="hybrid_search",
        novel_id=novel.id,
        config_snapshot={},
    )
    db_session.add_all([dataset, run])
    await db_session.commit()

    d_id, r_id = dataset.id, run.id

    # 删除 Novel，应级联删除 eval 数据
    await db_session.delete(novel)
    await db_session.commit()

    ds = await db_session.get(EvalDataset, d_id)
    rn = await db_session.get(EvalRun, r_id)
    assert ds is None
    assert rn is None


@pytest.mark.asyncio
async def test_eval_status_transitions(db_session: AsyncSession):
    """测试评测题状态流转"""
    _, novel = await _create_user_and_novel(db_session, "status_test")

    dataset = EvalDataset(
        novel_id=novel.id,
        question="状态测试题",
        gold_chunks=[],
        expected_points=[],
        must_not_say=[],
        status="candidate",
    )
    db_session.add(dataset)
    await db_session.commit()

    dataset.status = "confirmed"
    await db_session.commit()
    assert dataset.status == "confirmed"

    dataset.status = "rejected"
    await db_session.commit()
    assert dataset.status == "rejected"

    dataset.status = "candidate"
    await db_session.commit()
    assert dataset.status == "candidate"


@pytest.mark.asyncio
async def test_eval_result_error_case_flag(db_session: AsyncSession):
    """测试错误案例标记"""
    _, novel = await _create_user_and_novel(db_session, "error_test")

    dataset = EvalDataset(
        novel_id=novel.id,
        question="错误案例题",
        gold_chunks=[],
        expected_points=[],
        must_not_say=[],
    )
    run = EvalRun(
        run_name="错误案例运行",
        strategy="baseline_vector",
        novel_id=novel.id,
        config_snapshot={},
    )
    db_session.add_all([dataset, run])
    await db_session.flush()

    er = EvalResult(
        run_id=run.id,
        dataset_id=dataset.id,
        recalled_chunks=[],
        score=0.0,
        metrics={"recall@5": 0.0, "precision@5": 0.0},
        is_error_case=True,
    )
    db_session.add(er)
    await db_session.commit()

    result = await db_session.get(EvalResult, er.id)
    assert result.is_error_case is True
    assert result.score == 0.0
    assert len(result.recalled_chunks) == 0
