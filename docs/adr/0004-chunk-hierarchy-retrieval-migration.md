# ADR-0004: 从平铺零重叠切块迁移至三层层级化语义检索 (Migration to 3-Tier Chunk Hierarchy with Scene Expansion)

- **Status**: Accepted
- **Date**: 2026-08-24
- **Author**: Core AI & RAG Engineering Team
- **Decision Drivers**: 解决旧版分块零重叠导致的剧情撕裂、代词指代丢失与 20% 检索物理漏检问题。

---

## 1. Context & Problem Statement (背景与问题诊断)

在长篇网文（如《关于我转生成史莱姆这件事》《龙族》《我将埋葬众神》等千万字作品）的真实生产检索中，评测系统（Run ID: 13）暴露出了 **20.00% 的物理检索漏检率（Recall@5 仅 80.00%）**。

深入代码诊断（[`backend/app/services/chunking_service.py`](file:///d:/ADLINK/Myproject/novel-mind-new/backend/app/services/chunking_service.py)）后，确认该缺陷源于初代平铺分块算法（Legacy Chunker）的四大结构性死穴：
1. **零重叠硬截断（Zero Overlap Greedy Cut）**：按 300~500 字贪心累加，一旦超限立刻强制切断且无滑动重叠（Overlap = 0），导致关键战役或名场面的因果论据（如“施法者”与“杀敌结果”）被腰斩在两个相邻切块中；
2. **代词指代丢失（Coreference Lost）**：切块开头常出现“他/她/它拔出村雨”等孤立代词，脱离前文后导致精准关键词索引失效；
3. **未闭合台词撕裂（Unbalanced Quotes）**：在激烈对话中途按字数强行切断，造成人物台词被劈成两半；
4. **结构层级缺失**：所有切块扁平存储在 `text_chunks` 表中，大模型检索时只能拿到残缺的 300 字碎片，丢失了 900~1500 字的完整戏剧冲突场景。

---

## 2. Decision (架构决策)

我们决定正式废弃直接查询 `text_chunks` 扁平表的模式，全面迁移至 **Phase 07 三层层级化语义检索架构（Chunk Hierarchy Engine with Parent Scene Expansion）**：

```mermaid
graph TD
    subgraph 3-Tier Hierarchy Data Structure (chunk_hierarchy_nodes)
        L1[Level 1: Chapter 节点 - 章节全局摘要] --> L2_1[Level 2: Scene 场景节点 A<br>900~1500 字 完整戏剧冲突]
        L1 --> L2_2[Level 2: Scene 场景节点 B<br>900~1500 字 完整时空转场]
        L2_1 --> L3_1[Level 3: Evidence 原子切片 1 (300字)]
        L2_1 --> L3_2[Level 3: Evidence 原子切片 2 (300字)]
    end

    subgraph Dual-Stage Retrieval & Expansion Pipeline
        Q[用户提问 / 质检 Query] --> BM25[Evidence 级别精准向量与 BM25 匹配]
        BM25 --> TopK[命中 Top-K Evidence 叶子节点]
        TopK --> ParentExp[Parent Scene Context Expansion<br>通过 parent_id 自动向上拉取所属 Scene]
        ParentExp --> ContextOut[向 LLM 提供 1500 字起承转合完整上下文]
    end
```

### 核心实现规则与边界防线：
1. **时空与视角感知切分（`rules.py`）**：
   - 识别 `_TIME_MARKERS`（“翌日”、“数日后”）与 `_LOCATION_MARKERS`（“来到”、“离开”），仅在剧情自然转场缝隙处落刀；
   - 探测人称代词变化（`POV_SHIFT`）与说话人切换（`SPEAKER_SHIFT`）；
2. **代词与引号避让机制**：
   - `COREFERENCE_RISK`：下一句以代词开头时，强行倾向 `merge` 合并，禁止在代词句首切断；
   - `OPEN_QUOTE`：引号未闭合时禁止切断，保护台词完整性；
3. **父子节点两阶段检索（Two-Stage Parent-Child Retrieval）**：
   - **检索阶段（Matching）**：在 Level 3 `evidence`（300 字高密度叶子）上进行快速倒排与向量打分；
   - **装配阶段（Assembly）**：通过 `parent_id` 自动向上解析并加载 Level 2 `scene`（900~1500 字完整剧情冲突），消灭上下文碎片感；
4. **不可变版本快照与指针锁定**：
   - 每次分块生成独立的 `chunk_builds`，通过 SHA-256 计算 `manifest_checksum`，并由 `chunk_active_pointers` 实现零停机原子切换与瞬时回滚。

---

## 3. Consequences & Impact (影响与收益)

### 正向收益 (Positive Impact)
- **消除因果撕裂**：父节点上下文扩展使得 LLM 始终能观察到前后 1500 字的完整因果链条；
- **检索准确率与召回率显著跃升**：在基准数据集上，物理检索召回率预计从 **80.00% 跃升至 95.00%+**；
- **版本可追溯与可回滚**：所有切块节点具备唯一 SHA-256 签名与来源偏移量映射（`source_start` / `source_end`）。

### 成本与权衡 (Trade-offs & Mitigations)
- **存储开销增加**：单本小说生成三层节点（54,000+ nodes），存储体积相比平铺切块上升约 2.5 倍（PostgreSQL 磁盘开销从 ~15MB 增至 ~38MB，完全在可控承受范围内）；
- **上下文 Token 预算**：返回 Scene 上下文使传入 LLM 的 Token 略有增加，需结合 Dynamic Context Window 限制 Top-K 数量。

---

## 4. References (相关代码与规范)
- [`backend/app/services/chunking/rules.py`](file:///d:/ADLINK/Myproject/novel-mind-new/backend/app/services/chunking/rules.py)
- [`backend/app/services/chunking/hierarchy.py`](file:///d:/ADLINK/Myproject/novel-mind-new/backend/app/services/chunking/hierarchy.py)
- [`backend/app/services/chunking/adjudicator.py`](file:///d:/ADLINK/Myproject/novel-mind-new/backend/app/services/chunking/adjudicator.py)
- [`backend/app/models/chunk_build.py`](file:///d:/ADLINK/Myproject/novel-mind-new/backend/app/models/chunk_build.py)
