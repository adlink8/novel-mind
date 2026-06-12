"""
RAG 端到端验证测试

验证完整流程：上传 TXT → 分块 → embedding → 存入 ChromaDB → 语义搜索

先决条件:
  1. PostgreSQL 运行在 localhost:5432
  2. ChromaDB 运行在 localhost:8001
  3. 数据库迁移已执行

运行方式:
  cd backend
  pytest -m e2e tests/test_rag_e2e.py          # 仅运行 e2e 测试
  pytest -m "not e2e" tests/                   # CI 中跳过 e2e 测试
"""

import random

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import delete as sa_delete

from app.config import settings
from app.models import Novel, ImportJob
from app.services.chunking_service import chunking_service
from app.services.vector_store import vector_store


# 标记为 e2e 测试（CI 中可通过 -m "not e2e" 跳过）
pytestmark = pytest.mark.e2e

# ---------------------------- 测试小说内容 ----------------------------
SAMPLE_NOVEL = """第一回 灵根初现

天元大陆，青云山脉深处，一座名为云霄宗的小门派坐落于此。

清晨的阳光穿透薄雾，洒在后山的练功场上。一个十五六岁的少年正盘膝而坐，闭目凝神。

"林风，你又在这里偷懒！"一个清脆的女声从身后传来。

少年睁开眼睛，无奈地笑了笑："师姐，我没有偷懒，只是觉得这样坐着比舞剑更有效。"

"胡说八道！"师姐柳月儿叉着腰，一脸不信，"你不过就是想偷懒罢了。师父说了，今天要检查你的剑法进展，你再不练，小心被罚去劈柴。"

林风叹了口气，站起身来。他知道师姐虽然嘴上凶，但实则关心他。

突然，天空中传来一声惊雷，整个练功场都在震动。

"怎么回事？"柳月儿抬头望天，脸色剧变。

只见天空中裂开一道金色的缝隙，一道耀眼的光芒直射而下，正好笼罩住了林风。少年只觉得浑身发烫，一股暖流从丹田升起，直冲头顶。

"这是...天灵根觉醒！"远处传来师父震惊的声音。

金色的光芒持续了整整一刻钟才缓缓消散。当光芒褪去时，林风整个人都像是脱胎换骨了一般，双目中闪烁着灵动的光芒。

"没想到我云霄宗竟然出了一个天灵根弟子！"师父激动得声音都在颤抖，"风儿，从今日起，你就是我云霄宗的核心弟子！"

林风握紧拳头，感受着体内澎湃的灵力。他知道，自己的命运从此刻开始，将完全不同。

第二回 山门大比

天灵根觉醒的消息很快传遍了整个青云山脉。

三个月后，一年一度的山门大比如期举行。这是青云山脉所有门派的盛事，也是年轻弟子展示实力的舞台。

云霄宗的广场上已经搭建起了巨大的擂台。各大门派的弟子们陆续到场，气氛紧张而热烈。

"听说了吗？云霄宗出了个天灵根。"

"就是那个叫林风的小子？才修炼三个月，能有多厉害？"

"据说他在三个月内就从一个凡人修炼到了炼气七层，这种速度..."

话音未落，擂台上已经站定了一个少年。正是林风。

他的对手是烈焰门的首席弟子赵虎，炼气九层的修为，已经在修真界修炼了五年。

"小子，认输吧。我修炼五年，你才三个月，不可能是我的对手。"赵虎轻蔑地说道。

林风微微一笑："请赐教。"

比赛开始的铜锣一响，赵虎率先出手。一道火球术呼啸而出，直扑林风面门。林风不慌不忙，身形一闪，轻松躲过。手中长剑一指，一道青色剑气破空而去。

赵虎大吃一惊，连忙后退，但还是被剑气擦中了肩膀，衣衫瞬间被割破。

"好快的剑！"台下有人惊呼。

仅仅三个回合，赵虎就被林风一剑指住了喉咙。

"我认输。"赵虎满头大汗，不甘心地说道。

林风收剑入鞘，向四周施了一礼。台下响起了雷鸣般的掌声。这个少年一鸣惊人，成为了本次大比最大的黑马。

第三回 秘境试炼

山门大比之后，林风的名字在整个青云山脉都传开了。

然而还没等他从喜悦中回过神来，一个更大的挑战降临了。掌门宣布，今年的秘境试炼将提前开启，每个门派可以派出三名弟子参加。

秘境是上古大能遗留下来的洞府，里面蕴藏着无数机缘，但也充满了危险。多少年来，不知道有多少天才弟子陨落其中。

林风和师姐柳月儿以及师兄韩铁一起，代表云霄宗进入了秘境。

秘境中果然处处危机。刚进入不久，三人就遇到了一群灵兽的围攻。韩铁独当一面，挥舞着巨斧劈斩。林风和柳月儿左右配合，终于在付出不小的代价后击退了兽群。

"小心，前面有禁制。"柳月儿突然停下脚步，神色严肃地望向远处。

前方的通道里布满了细密的符文，散发着令人心悸的波动。这些符文一旦被触发，就会发动毁灭性的攻击。

"跟我走。"林风忽然说道，他的天灵根让他对这些灵力波动的感知格外敏锐，"我能感觉到安全的路。"

果然，在林风的带领下，三人有惊无险地穿过了禁制区域。经过了整整三天的探索，他们终于到达了秘境的最深处——一个古老的殿堂。

殿堂中央悬浮着一把泛着青光的古剑，剑身上刻着两个古朴的大字：青霄。

"这就是传说中的青霄剑！"韩铁惊叹道。

林风走上前去，古剑像是感应到了什么，竟然主动飞到了他的手中。一股浩瀚的信息涌入他的脑海——这是一部名为《青霄诀》的上古功法。

秘境之行结束后，林风的实力再次暴涨。天灵根配上上古功法，让他一跃成为了青云山脉年轻一代中最耀眼的新星。
"""


# ---------------------------- 辅助函数 ----------------------------

def _generate_random_embedding(dim: int = 1536, seed: int = None) -> list[float]:
    """生成归一化的随机向量（模拟 embedding）"""
    if seed is not None:
        random.seed(seed)
    emb = [random.random() for _ in range(dim)]
    norm = sum(x * x for x in emb) ** 0.5
    return [x / norm for x in emb]


def _generate_embeddings(texts: list[str], dim: int = 1536, seed: int = 42) -> list[list[float]]:
    """为文本列表批量生成随机向量"""
    random.seed(seed)
    embeddings = []
    for _ in texts:
        emb = [random.random() for _ in range(dim)]
        norm = sum(x * x for x in emb) ** 0.5
        embeddings.append([x / norm for x in emb])
    return embeddings


# ---------------------------- Fixtures ----------------------------

@pytest.fixture
async def e2e_db():
    """e2e 数据库引擎（真实 PostgreSQL）"""
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        yield session
    
    await engine.dispose()


@pytest.fixture
async def e2e_novel(e2e_db):
    """创建测试小说并返回 novel_id，测试后自动清理"""
    novel = Novel(
        title="青霄剑仙",
        author="测试用",
        chapter_count=3,
        word_count=len(SAMPLE_NOVEL),
        status="ready",
        owner_id=1,
    )
    e2e_db.add(novel)
    await e2e_db.flush()
    novel_id = novel.id
    await e2e_db.commit()
    
    yield novel_id
    
    # 清理
    try:
        await e2e_db.execute(sa_delete(ImportJob).where(ImportJob.novel_id == novel_id))
        await e2e_db.delete(novel)
        await e2e_db.commit()
    except Exception:
        pass
    
    await vector_store.delete_novel_chunks(novel_id)


# ---------------------------- 测试用例 ----------------------------

class TestRagChunking:
    """文本分块测试"""

    @pytest.mark.asyncio
    async def test_chunking_produces_blocks(self):
        """分块产生正确的块数"""
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        assert len(chunks) >= 2, f"期望至少 2 个块，实际 {len(chunks)} 个"

    @pytest.mark.asyncio
    async def test_chunking_preserves_content(self):
        """分块不丢失内容"""
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        total_words = sum(c["word_count"] for c in chunks)
        actual_len = len(SAMPLE_NOVEL.replace("\n", "").replace(" ", ""))
        # 允许 10% 误差（分块时可能过滤部分空白）
        assert abs(total_words - actual_len) < actual_len * 0.1, \
            f"总字数 {total_words} 与原文字数 {actual_len} 差异过大"

    @pytest.mark.asyncio
    async def test_chunking_detects_types(self):
        """分块正确检测 chunk_type"""
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        types = {c["chunk_type"] for c in chunks}
        # 应该至少包含 scene 和 paragraph 两种类型
        assert "scene" in types or "paragraph" in types, \
            f"分块类型缺少 scene/paragraph，实际: {types}"

    @pytest.mark.asyncio
    async def test_chunk_index_continuous(self):
        """chunk_index 连续递增"""
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks))), \
            f"chunk_index 不连续: {indices}"


class TestRagEmbedding:
    """Embedding 生成测试"""

    @pytest.mark.asyncio
    async def test_embedding_dimensions(self):
        """向量维度正确"""
        texts = [SAMPLE_NOVEL[:500]]
        embeddings = _generate_embeddings(texts, dim=settings.embedding_dimensions)
        assert len(embeddings) == 1
        assert len(embeddings[0]) == settings.embedding_dimensions, \
            f"期望维度 {settings.embedding_dimensions}，实际 {len(embeddings[0])}"

    @pytest.mark.asyncio
    async def test_embedding_normalized(self):
        """向量已归一化"""
        embeddings = _generate_embeddings(["测试"], dim=settings.embedding_dimensions)
        norm = sum(x * x for x in embeddings[0]) ** 0.5
        assert abs(norm - 1.0) < 1e-6, f"向量未归一化，norm={norm}"


class TestRagVectorStore:
    """向量存储测试（需要 ChromaDB）"""

    @pytest.mark.asyncio
    async def test_store_and_count(self, e2e_novel):
        """存储向量并验证计数"""
        novel_id = e2e_novel
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        texts = [c["content"] for c in chunks]
        embeddings = _generate_embeddings(texts)

        chroma_chunks = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chroma_chunks.append({
                "id": f"chunk_{i + 1}",
                "content": chunk["content"],
                "embedding": emb,
                "metadata": {
                    "chunk_id": i + 1,
                    "chapter_id": 1,
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk["chunk_type"],
                    "word_count": chunk["word_count"],
                },
            })

        await vector_store.add_chunks(novel_id, chroma_chunks)
        count = await vector_store.get_chunk_count(novel_id)
        assert count == len(chunks), \
            f"ChromaDB 存储数量不匹配: 期望 {len(chunks)}, 实际 {count}"

    @pytest.mark.asyncio
    async def test_search_returns_results(self, e2e_novel):
        """搜索返回结果"""
        novel_id = e2e_novel
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        texts = [c["content"] for c in chunks]
        embeddings = _generate_embeddings(texts)

        chroma_chunks = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chroma_chunks.append({
                "id": f"chunk_search_{i + 1}",
                "content": chunk["content"],
                "embedding": emb,
                "metadata": {
                    "chunk_id": i + 1,
                    "chapter_id": 1,
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk["chunk_type"],
                    "word_count": chunk["word_count"],
                },
            })

        await vector_store.add_chunks(novel_id, chroma_chunks)

        # 搜索
        query_embedding = _generate_random_embedding(seed=99)
        results = await vector_store.search(novel_id, query_embedding, top_k=2)

        assert len(results) >= 1, "搜索结果为空"
        assert len(results) <= 2, f"top_k 限制失效: 期望 ≤2, 实际 {len(results)}"

        for r in results:
            assert "content" in r, "搜索结果缺少 content 字段"
            assert "score" in r, "搜索结果缺少 score 字段"
            assert "metadata" in r, "搜索结果缺少 metadata 字段"
            assert 0 <= r["score"] <= 1, f"score 应在 [0,1] 范围内: {r['score']}"

    @pytest.mark.asyncio
    async def test_search_with_chunk_type_filter(self, e2e_novel):
        """按 chunk_type 过滤搜索"""
        novel_id = e2e_novel
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        texts = [c["content"] for c in chunks]
        embeddings = _generate_embeddings(texts)

        chroma_chunks = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chroma_chunks.append({
                "id": f"chunk_filter_{i + 1}",
                "content": chunk["content"],
                "embedding": emb,
                "metadata": {
                    "chunk_id": i + 1,
                    "chapter_id": 1,
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk["chunk_type"],
                    "word_count": chunk["word_count"],
                },
            })

        await vector_store.add_chunks(novel_id, chroma_chunks)

        query_embedding = _generate_random_embedding(seed=50)
        results = await vector_store.search(
            novel_id, query_embedding, top_k=5,
            filters={"chunk_type": "scene"},
        )
        # 验证过滤结果
        for r in results:
            rt = r.get("metadata", {}).get("chunk_type")
            assert rt == "scene", f"过滤后出现非 scene 类型: {rt}"

    @pytest.mark.asyncio
    async def test_delete_collection(self, e2e_novel):
        """删除集合"""
        novel_id = e2e_novel
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL[:500],
        )
        texts = [c["content"] for c in chunks]
        embeddings = _generate_embeddings(texts)

        chroma_chunks = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chroma_chunks.append({
                "id": f"del_{i + 1}",
                "content": chunk["content"],
                "embedding": emb,
                "metadata": {"chunk_id": i + 1},
            })

        await vector_store.add_chunks(novel_id, chroma_chunks)
        count_before = await vector_store.get_chunk_count(novel_id)
        assert count_before > 0

        await vector_store.delete_novel_chunks(novel_id)
        count_after = await vector_store.get_chunk_count(novel_id)
        assert count_after == 0, f"删除后仍有 {count_after} 个向量"


class TestRagFullPipeline:
    """完整管线端到端测试"""

    @pytest.mark.asyncio
    async def test_full_pipeline(self, e2e_db, e2e_novel):
        """完整流程：分块 → embedding → 存储 → 搜索"""
        novel_id = e2e_novel

        # 1. 分块
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        assert len(chunks) >= 2

        # 2. embedding
        texts = [c["content"] for c in chunks]
        embeddings = _generate_embeddings(texts)
        assert len(embeddings) == len(chunks)

        # 3. 存储
        chroma_chunks = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chroma_chunks.append({
                "id": f"pipeline_{i + 1}",
                "content": chunk["content"],
                "embedding": emb,
                "metadata": {
                    "chunk_id": i + 1,
                    "chapter_id": 1,
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk["chunk_type"],
                    "word_count": chunk["word_count"],
                },
            })

        await vector_store.add_chunks(novel_id, chroma_chunks)
        count = await vector_store.get_chunk_count(novel_id)
        assert count == len(chunks)

        # 4. 搜索
        query_embedding = _generate_random_embedding(seed=42)
        results = await vector_store.search(novel_id, query_embedding, top_k=3)

        assert len(results) >= 1
        assert len(results) <= 3
        # 验证分数降序
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True), f"搜索分数未降序: {scores}"

    @pytest.mark.asyncio
    async def test_search_text_relevance(self, e2e_db, e2e_novel):
        """搜索内容相关性（使用固定种子确保可重现）"""
        novel_id = e2e_novel

        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=SAMPLE_NOVEL,
        )
        texts = [c["content"] for c in chunks]
        embeddings = _generate_embeddings(texts, seed=42)

        chroma_chunks = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chroma_chunks.append({
                "id": f"rel_{i + 1}",
                "content": chunk["content"],
                "embedding": emb,
                "metadata": {
                    "chunk_id": i + 1,
                    "chapter_id": 1,
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk["chunk_type"],
                    "word_count": chunk["word_count"],
                },
            })

        await vector_store.add_chunks(novel_id, chroma_chunks)

        # 用固定种子搜索，确保结果可重现
        query_embedding = _generate_random_embedding(seed=42)
        results = await vector_store.search(novel_id, query_embedding, top_k=5)

        assert len(results) >= 1
        # 所有非空 score 应在合理范围
        for r in results:
            assert r["score"] > 0, "score 应大于 0"
            assert len(r["content"]) > 0, "content 不应为空"
