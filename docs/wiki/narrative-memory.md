# 叙事记忆（Narrative Memory）

## 是什么

叙事记忆（NM）是项目对小说**最深层的知识表示**。它不是原始文本——而是 AI 提取的**结构化 Claims**，按四层等级组织，回答「故事真正的叙事结构是什么」。

对比时间线：

| 维度 | 时间线 | 叙事记忆 |
|---|---|---|
| 粒度 | 事件级 | Claim 级（状态/关系/线索/loop） |
| 类型 | 一句话概括 | 带不确定性的结构化断言 |
| 组织 | 平铺的事件列表 | 四层等级树（global → volume → arc → chapter） |
| 版本 | 可选 | **强制不可变**——每个版本 sealed，可审计 |
| 预算 | 低（测试小说约 3 分钟提取） | 高（每章一次 LLM 调用 + 合并层） |

## 四层等级结构

```
global_story（全书叙事）        ← 全书的综合知识图谱
  └── volume（卷）             ← 多弧集合（如「东海篇」「新世界篇」）
        └── story_arc（故事弧） ← 多章故事线（如「大闹天宫」）
              └── chapter_state（章节状态） ← 单章知识总结（每章一个）
```

## 六种 Claims

| Claim 类型 | 记录什么 | 示例 |
|---|---|---|
| `entity_state` | 角色/地点/物品/势力的状态变化 | 孙悟空：位置从花果山→天宫，身份从猴王→弼马温 |
| `event_fact` | 发生的事实 | 孙悟空偷吃蟠桃，导致被天兵追捕 |
| `relationship_delta` | 关系变化 | 孙悟空 vs 玉帝：从招安→敌对 |
| `clue_delta` | 线索生命周期 | 金箍棒：埋设（第1章）→ 回收（第7章大闹天宫） |
| `world_state_delta` | 政治/社会/环境变化 | 天庭秩序：稳定→受威胁 |
| `open_loop_delta` | 未解决的叙事线索 | 孙悟空身世之谜：打开→揭晓 |

每个 Claim 自带：
- `uncertainty`：certain / likely / uncertain / unknown
- `confidence`：[0.0, 1.0]
- `visible_from_chapter`：剧透门控章节（在此之前对读者不可见）
- `source_keys`：回原文证据链

## Builder 流水线（5 阶段）

```
CHAPTER_STATE          → 每章提取一个 chapter_state 节点的所有 claims
ARC_VOLUME_PLAN        → 规划哪些章节归为弧/卷（LLM 决定聚合方案）
ARC_VOLUME_AGGREGATE   → 将底层 claims 向上汇聚到弧/卷（去重、冲突解决）
GLOBAL_AGGREGATE       → 全书级综合（所有弧/卷的 claim 合并为 global_story）
MANIFEST_VALIDATION    → 校验一致性、封缄不可变版本
```

### Stage 1：CHAPTER_STATE（当前唯一完成的阶段）

对每章，发送给 LLM：
- 该章的完整原始正文（evidence）
- 从上一章 carry forward 过来的 open loops / entity states
- 要求按严格 Schema 输出该章节下所有 claims

预算控制：支持 token/费用上限，超限自动切换到 `partial` 状态。

### Stage 2-5（**搁置中**，因预算/优先级原因未执行）

实际只跑了约 12 章 chapter_state（STATE.md 记录），后续阶段的代码已写好但未被调度。恢复构建可运行 `_nm_resume_loop.py`。

## 不可变版本与审计

每个 NM 版本被构建后即 **sealed**：
- `NarrativeMemoryVersion` 记录全部 input 谱系（prompt hash + schema hash + model hash + config hash）
- `NarrativeMemoryManifest` 记录 checksum（全部 component counts + 校验和）
- `NarrativeMemoryValidationReport` 记录 qualified / blocked 裁决
- **没有任何 promote 操作**——NM 目前仅为 candidate_preview，不驱动任何生产数据

## 关键代码位置

| 文件 | 职责 |
|---|---|
| `builder_contracts.py` | Builder 控制层 DTO（BudgetPolicy, RunPolicy, StageKind） |
| `builder_worker.py` | Builder 执行引擎（调度、并发、暂停/恢复） |
| `global_builder.py` | GLOBAL_AGGREGATE 阶段 |
| `arc_planner.py` | ARC_VOLUME_PLAN 阶段 |
| `carry_forward.py` | 跨章 carry forward（open loops / entity states） |
| `descent.py` | 多阶段 execution plan（阶段间的依赖图） |
| `contracts.py` | 核心 DTO（CandidatePackage, NodeKind, ClaimType） |
| `authority.py` | 版本权限控制 |
| `qualification_*` | 验证与封缄管线 |

---

> **常见追问**：NM 也是 LLM 调用吗？Stage 1 具体发了什么？为什么树是平的？→ [FAQ](faq.md#叙事记忆nm)
