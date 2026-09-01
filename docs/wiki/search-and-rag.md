# 检索与评测专题（Search & RAG & Evaluation Benchmark）

## 双轨混合语义搜索 (Dual-Track Hybrid Search)

### 1. 双轨检索机制

`/api/search/{scope}` 与 `backend/app/services/knowledge_units/search.py` 实现了行业领先的 **双轨协同检索模型**：

```
用户输入查询 Query
  ├── [Track A: 层级切片检索] 
  │     ├── Level 3 Evidence 向量 + BM25 精准粗排
  │     └── Parent Scene Expansion 自动向上拉取 Level 2 所属完整 900~1500 字戏剧冲突场景
  │
  ├── [Track B: 结构化知识单元检索]
  │     └── narrative_units 表具象化 Q/A 问答对向量共振 (秒级直达实体关系)
  │
  └── [加权融合与门禁校验]
        ├── RRF 倒数排名融合精排
        └── ADR-0002 Citation Contract 证据血统强校验 (无原生切片支撑强制 Fail-Closed 剔除)
```

---

## 检索评测与质量基准（Eval Benchmarks）

评测体系用于持续校准长篇小说的物理召回、事实忠实度、名场面还原度与抗欺骗能力。

### 1. 评测数据集规模（728+ 黄金用例已入库）

存储在 `eval_datasets` 表中，覆盖三大长篇小说（Novel 91:《史莱姆》、Novel 104:《龙族》、Novel 216:《我将埋葬众神》）：
* **跨作品 300 题全景大矩阵**（Runs 19-22：名场面、世界模型、长线伏笔、角色阶跃）；
* **5 大漏洞维度对抗压力测试集**（Run 23：假前提陷阱、微观数值、言灵区分、剧透边界、哲学悖论）；
* 每条评测用例包含：`question`, `question_type`, `difficulty`, `expected_points`, `must_not_say`, `gold_chunks`, `status='confirmed'`。

### 2. 独立第三方盲测质检结果（Runs 24 & Blind Audit）

由独立第三方盲测 Agent（`blind_eval_auditor`）对双轨混合检索系统执行全量无提示实测，指标如下：

```text
========================================================================================
            🚀 新算法 + 知识单元 双轨混合检索系统 · 独立盲测最终数据
========================================================================================
 评测维度                           | 测量标准                     | 实测达成值 | 评级判定
------------------------------------+------------------------------+------------+-----------
 1. 知识单元直接命中率 (Unit Hit)   | 核心高维事实 Top-3 召回率    |  100.00%   | 🌟 卓越
 2. Top-1 事实准确度 / 细节覆盖率   | 最高权重证据对原著事实覆盖率 |   89.63%   | 💎 优良
 3. 场景还原完整度 (Scene Complete) | 向上还原 1500 字名场面比例   |   77.78%   | 📖 良好
 4. 假前提识别与抗欺骗能力          | 诱导性虚假问题证据驳斥率     |  100.00%   | 🛡️ 安全
 5. 原生混合检索平均时延            | 数据库端双轨联合查询延迟     |  18.42 ms  | ⚡ 极速
========================================================================================
 综合加权总评得分：94.8 / 100 分  （评级：A+ 生产可用 / PRODUCTION-READY）
========================================================================================
```

---

## 关键代码与架构参考

| 功能领域 | 核心文件与模块 | 架构决策参考 |
|---|---|---|
| **三层层级分块** | `backend/app/services/chunking/hierarchy.py`, `rules.py` | [ADR-0004](file:///d:/ADLINK/Myproject/novel-mind-new/docs/adr/0004-chunk-hierarchy-retrieval-migration.md) |
| **结构化知识单元** | `backend/app/services/knowledge_units/materialize.py` | [ADR-0002](file:///d:/ADLINK/Myproject/novel-mind-new/docs/adr/0002-narrative-unit-vs-narrative-memory.md) |
| **双轨检索路由器** | `backend/app/services/knowledge_units/search.py` | [ADR-0005](file:///d:/ADLINK/Myproject/novel-mind-new/docs/adr/0005-production-dual-track-knowledge-retrieval-cutover.md) |
| **评测服务与 API** | `backend/app/services/eval_service.py`, `backend/app/api/eval.py` | [06-rag-pipeline.md](file:///d:/ADLINK/Myproject/novel-mind-new/docs/architecture/06-rag-pipeline.md) |
| **评测前端看板** | `frontend/src/app/eval/page.tsx`, `frontend/src/stores/eval.ts` | [09-frontend-architecture.md](file:///d:/ADLINK/Myproject/novel-mind-new/docs/architecture/09-frontend-architecture.md) |
