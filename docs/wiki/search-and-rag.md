# 检索与评测（Search & RAG）

## 语义搜索

### 搜索策略

`/api/search/{scope}` 支持两种模式：

| 参数 | 效果 |
|---|---|
| `strategy=bm25` | PostgreSQL 全文搜索（`tsvector`），关键词匹配 |
| `strategy=hybrid_search`（默认） | BM25 + 向量搜索加权合并 |

混合搜索流程：
```
用户输入查询
  ├── BM25 搜索 → TextChunk.tsvector 字段
  ├── 向量搜索 → ChromaDB（从 TextChunk.embedding 字段检索）
  └── 加权合并 → 返回 top_k 结果
```

### 向量来源

导入时 `indexing_service.index_novel()` 自动完成：
1. `chunking_service.chunk_novel()` → 切成 300-500 字语块
2. `ai_service.embedding()` → 调 AI 模型算向量
3. `vector_store.add_chunks()` → 存入 ChromaDB

## 检索评测（Eval）

评测是为了持续校准搜索召回质量。

### 评测数据集

存储在 `eval_datasets` 表中，每行是一条测试题目：
- `question`：问题
- `question_type`：类型（`original_text` / `character_relation` / `event_causality` / `timeline` / `foreshadowing`）
- `difficulty`：简单 / 中等 / 困难
- `gold_chunks`：期望召回的正确语块 ID 列表
- `expected_points`：期望回答中出现的要点
- `status`：`candidate`（未确认）→ `confirmed`（确认后可评测）

### 评测运行

1. 用户筛选并确认测试题目（`candidate → confirmed`）
2. 调用 `POST /api/eval/runs` 创建评测运行
3. 系统自动运行三种策略（`bm25` / `baseline_vector` / `hybrid_search`）
4. 每种策略对每题执行检索 → 与 `gold_chunks` 比对
5. 计算指标存入 `eval_runs`：

| 指标 | 含义 |
|---|---|
| `recall_at_k` | 前 k 块中召回正确 gold_chunks 的比例 |
| `precision_at_k` | 前 k 块中正确块的比例 |
| `mrr` | 首个正确答案的平均倒数排名 |
| `ndcg_at_k` | 归一化折损累计增益 |
| `latency_ms` | 检索延迟 |

### 质量评测（新增）

`/api/eval/quality/runs` 支持计算：
- `answer_faithfulness`：AI 回答是否忠于检索到的上下文
- `context_recall`：检索到的上下文是否覆盖正确答案所需的所有信息

### 当前状态

- 数据集：约 32 条 candidate，10 条 confirmed
- 历史运行：6 次 legacy 运行，指标均为 **0**（所有策略均未召回任何 gold_chunks）
- 质量评测：已实现但 metrics 为 null（上下文召回率为 0 → 无可比较指标）

### 评测数据集生成

命令行脚本 `scripts/generate_eval_candidates.py`：
- 从导入的小说中取随机段落
- 调用 LLM 根据段落生成问题
- 问题类型覆盖 5 类（原文定位 / 人物关系 / 事件因果 / 时间线 / 伏笔）
- 自动标注 gold_chunks（段落所在语块 ID）和 difficulty

## 关键代码位置

| 功能 | 后端文件 |
|---|---|
| 搜索 API | `backend/app/api/search.py` |
| 评测 API | `backend/app/api/eval.py` |
| 索引管线 | `backend/app/services/indexing_service.py` |
| 向量存储 | `backend/app/services/vector_store.py` |
| 评测前端 | `frontend/src/app/eval/page.tsx` |
| 评测 store | `frontend/src/stores/eval.ts` |
