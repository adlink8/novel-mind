---
analysis_type: expected-target-gap
project: NovelMind
date: 2026-07-17
status: current_estimate
method: GSD project/requirements/milestone audit + live code/database/test observations
related_audit: ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md
estimate_policy: percentages are planning estimates, not release metrics
---

# NovelMind 预期目标差距分析

## 1. 分析目的

本文件回答三个不同问题，避免把它们混成一个“完成率”：

1. **底层架构是否足以支持未来长期演进？**
2. **当前已声明里程碑是否达到其限定目标？**
3. **距离完整的 AI 小说理解与创作产品还有多远？**

项目的核心价值是：

> 在可信、安全、可迁移的基础上构建 AI 小说理解与创作能力。功能数量不能替代权限、数据一致性和可重复验证。

因此，目标差距不能只根据 Phase 或代码数量判断，而应分别计算：

```text
Architecture readiness
Data readiness
Quality evidence
Operational readiness
Product capability
```

---

# 2. 目标层级

## Target A — 底层架构与数据治理基础

预期结果：

- 原文是唯一不可替代事实源；
- Evidence、Scene、Chapter State、Arc、Global 层级职责明确；
- Timeline、Relationship、Clue 是可追溯 Facet，而不是第二真相源；
- PostgreSQL、Chroma、Neo4j 的 authority、projection、rebuild 边界固定；
- Candidate、Qualification、Promotion、Active、Rollback 分离；
- 小说局部变化只触发局部重建；
- 所有上层结论可以回落到叶子原文；
- 测试、构建、评测和文档可以绑定同一代码与数据快照。

## Target B — v0.8 Candidate-Only 分层叙事记忆里程碑

官方限定目标：

- 单书、候选态、无生产切换；
- Chapter State → Arc/Volume → Global 的底向上构建能力；
- 分层检索和 leaf/raw fallback；
- dependency-aware rebuild；
- frozen qualification；
- 不创建 Narrative Memory Active Pointer；
- 不切换 Reader Chat、Timeline、Relationship 或 Clue 消费者。

## Target C — 可稳定使用的小说理解产品

预期结果：

- 用户导入任意长篇后，可以稳定完成整书结构化；
- 能回答局部、跨章、全局、人物、时间线、关系和伏笔问题；
- 结果具有可靠引用、阅读进度隔离和无答案能力；
- Reader Chat、分析工作台和结构工作台形成统一消费路径；
- 质量、成本、延迟和失败恢复达到可重复门禁。

## Target D — 完整 AI 小说理解与创作平台

预期结果：

- 可靠理解整书结构、人物演化、因果链、线索铺设与回收；
- 支持阅读问答、研究分析、续写、同人创作、编辑、版本和导出；
- 可以基于原作证据保持人物、世界观、时间线和文风一致；
- 创作结果与分析事实分离，禁止创作内容污染原作知识层。

---

# 3. 总体距离估算

下表中的百分比是基于当前代码、数据库、测试和里程碑边界形成的规划估算，不是正式 KPI。

| 目标 | 当前估计 | 剩余差距 | 判断 |
|---|---:|---:|---|
| Target A：底层架构与治理基础 | **70%–78%** | 22%–30% | 主方向正确，边界与一致性尚未完全收口 |
| Target B：v0.8 candidate-only 契约实现 | **95%–100%** | 0%–5% | 官方审计已判定 achieved_candidate_scope |
| Target B：真实单书候选数据跑通度 | **25%–35%** | 65%–75% | 代码能力已建，515 章真实构建仍 partial |
| Target C：稳定小说理解产品 | **55%–65%** | 35%–45% | 导入、检索、工作台已具备；跨章理解和质量闭环不足 |
| Target D：完整理解与创作平台 | **45%–55%** | 45%–55% | 创作出口、关系演化、因果和伏笔闭环仍缺 |

必须同时保留两个结论：

> v0.8 的 candidate-only 里程碑按限定契约已经完成。

以及：

> 完整产品目标和真实长篇数据目标仍远未完成。

两者不矛盾。

---

# 4. Target A：底层架构差距

## 4.1 当前已经具备

| 能力 | 当前状态 |
|---|---|
| 原文与派生层分离 | 已具备 |
| PostgreSQL 权威、向量/图为派生 | 原则已明确 |
| Evidence lineage | 已具备 |
| Candidate/Active 分离 | Narrative Unit 已具备；NM candidate-only |
| 版本、manifest、qualification | 已具备较完整骨架 |
| 局部 rebuild 与 carry-forward | 已实现合同和测试 |
| leaf/raw fallback | 已实现合同和测试 |
| owner/spoiler 边界 | 已有较强自动化覆盖 |

## 4.2 距离“底层搭稳”仍差什么

| 缺口 | 当前问题 | 达标状态 |
|---|---|---|
| 统一层级注册表 | `L0-L6` 在不同文档含义不同 | 建立 `S/D/R/A` 权威 ADR |
| Narrative Unit 与 Narrative Memory 边界 | 两套叙事模型、两套版本/索引语义接近 | 固定用途、替代关系和消费顺序 |
| 存储一致性 | PostgreSQL/Chroma 无原子事务 | journal + manifest + reconcile + fail-closed |
| Facet 权威 | Timeline/Relationship/Clue 与主结构边界分散 | 固定只读投影和禁止反馈环 |
| 生产 Promotion 契约 | NM 当前故意不存在 | 新里程碑才可设计，不能直接补开关 |
| 数据 readiness 指标 | Phase complete 与数据覆盖混用 | 独立报告 implementation/data/quality |
| 评测快照 | 文档记录与当前 DB 不一致 | 绑定 DB fingerprint、dataset、commit |
| 工程基线 | 测试、构建、WIP 未收口 | clean tree + all gates green + release manifest |

## 4.3 Target A 的退出条件

只有同时满足以下条件，才可称“底层架构搭建完成”：

1. 唯一 Layer Registry 被 PROJECT、REQUIREMENTS、API schema 和代码共同引用。
2. 每个上层表都明确 source authority、rebuildability、lineage 和 lifecycle。
3. PostgreSQL、Chroma、Neo4j 任一损坏都可由权威数据重建。
4. Candidate、Active、Rollback 不依赖人工猜测当前版本。
5. 单章修改只重建受影响 Scene/State/Arc/Global。
6. Facet 无法无证据反写主结构。
7. 当前 commit、DB snapshot、index manifest、eval report 一一对应。
8. 全部测试和生产构建通过，工作树产物分类受控。

当前尚未完全满足 1、2、3、4、6、7、8。

---

# 5. Target B：v0.8 Candidate-Only 差距

## 5.1 契约层已经达到的目标

官方 `v0.8-MILESTONE-AUDIT.md` 判定：

```text
achieved_candidate_scope
```

已验证：

- 只读资产审计；
- Candidate 版本合同；
- Durable bottom-up builder；
- 分层检索与 leaf citation；
- dirty closure 与 carry-forward；
- frozen qualification；
- 无 production pointer；
- 默认 experiment 关闭。

因此，不应把“没有生产切换”列为 v0.8 未完成项，因为它本来就是范围边界。

## 5.2 真实数据层仍有明显差距

小说 91 当前观测：

| 数据 | 当前值 |
|---|---:|
| 章节 | 515 |
| Chapter State completed | 117 |
| Failed stage | 33 |
| Pending stage | 366 |
| Arc nodes | 0 |
| Global nodes | 0 |
| Build status | partial |

这意味着：

```text
候选构建能力已实现
≠
整本候选数据已完成
```

真实数据层要达到“单书完整候选”还需：

1. 515 章全部进入明确终态；
2. 失败 chapter 可恢复或被明确隔离；
3. Arc/Global 实际生成；
4. 每个 Claim 证据闭包通过；
5. qualification 对真实候选运行；
6. 成本、token、失败和 reuse 报告完整；
7. 结果仍保持 candidate-only。

---

# 6. Target C：稳定小说理解产品差距

## 6.1 能力矩阵

| 子系统 | 当前估计 | 目标差距 |
|---|---:|---|
| 小说导入、章节、原文保存 | 85%–90% | 大文件、错误补偿和运维继续收口 |
| Chunk/Evidence/层级切分 | 80%–88% | 中文质量和生产一致性验证 |
| 基础 RAG | 60%–70% | 当前环境缺少有效完整评测证据 |
| 结构工作台 | 75%–82% | 与真实 Arc/Global 数据联动不足 |
| 时间线 | 50%–60% | 1,933 events 但 causal edge=0 |
| 人物关系 | 40%–50% | 41 条均 establish，无 change/end |
| 伏笔线索 | 20%–30% | payoff 状态机阻断、lifecycle/link 为 0 |
| Narrative Memory | 30%–40% 数据态 | Chapter State partial，Arc/Global 为 0 |
| Reader Chat | 60%–70% | 真实浏览器、真实长期使用和 NM 接入未完成 |
| 质量评测 | 40%–50% | 当前 DB 中 eval dataset/run 为 0 |
| 发布与运行基线 | 50%–60% | 前端构建失败、测试各 1 项失败、工作树脏 |

## 6.2 最关键的语义差距

### 事件不等于因果

当前可以提取大量事件，但尚不能可靠回答：

```text
为什么发生？
什么事件推动了后续变化？
哪条支线影响了主线？
```

### 关系不等于演化

当前可以建立人物关系，但尚不能稳定表达：

```text
建立 → 强化 → 冲突 → 断裂 → 和解
```

### 线索不等于伏笔闭环

当前可以发现 cue/payoff 候选，但状态机没有把模型判断转化成完整 lifecycle 和 payoff chain。

### 摘要不等于全书模型

Chapter State 只有部分数据，Arc/Global 尚未生成，无法证明高层模型比原始章节检索更有用。

## 6.3 Target C 达标条件

- 至少一部长篇全层构建完成；
- local/arc/global/no-answer/spoiler 评测均通过；
- 时间线具备可验证因果边；
- 关系存在真实 change/end；
- 线索存在 cue→reinforce→payoff/dismissed 链；
- Reader Chat 在上层不可用时正确 fallback；
- 所有答案最终引用原文；
- build/test/Playwright/DB/index gate 全绿；
- 成本和失败恢复达到可重复标准。

---

# 7. Target D：完整理解与创作平台差距

## 7.1 当前已具备的基础

- 小说导入与阅读；
- 基础搜索和 RAG；
- 时间线、关系、线索和结构工作台骨架；
- Reader Chat；
- 证据、权限、剧透和版本思想；
- 分层叙事记忆候选架构。

## 7.2 尚未完成的核心产品闭环

### 创作能力

当前 Fanfiction 创建和续写 API 仍返回 501，数据库中无作品。还缺：

- 创作项目模型；
- 富文本或 Markdown 编辑；
- 版本历史；
- 章节规划；
- 角色/世界观约束；
- 原作事实与创作设定隔离；
- Markdown/EPUB 等导出；
- 创作评测和版权/安全边界。

### 原作一致性

创作前必须先解决：

- 人物状态随章节变化；
- 世界状态和不可逆事件；
- 时间线因果；
- 伏笔状态；
- 已读/全书剧透边界；
- 原作事实与用户 override 冲突。

否则续写只是普通文本生成，不是 NovelMind 目标中的“基于理解的创作”。

### 双知识空间

完整产品必须分离：

```text
Original Canon
User Interpretation / Override
Fanfiction Canon
```

三者必须具有不同 authority、namespace、version 和 citation 规则，禁止同人内容污染原作分析。

---

# 8. 主要阻塞关系

```text
层级/SSOT/一致性未收口
    ↓
真实长篇上层数据不稳定
    ↓
无法形成可靠全书理解
    ↓
Reader Chat 无法安全使用高层记忆
    ↓
创作无法获得可靠人物/世界状态
    ↓
完整小说理解与创作目标无法实现
```

所以正确顺序不是先实现更多创作按钮，而是：

```text
架构契约
→ 数据完整构建
→ 质量证明
→ 生产消费
→ 创作空间
```

---

# 9. 推荐阶段划分

## Stage 1 — Foundation Closure

- 统一 `S/D/R/A` Layer Registry；
- 收口 Narrative Unit/NM；
- 固定 storage/projection authority；
- 修复 Clue lifecycle；
- 修复构建与测试基线；
- 建立当前评测数据快照。

## Stage 2 — One-Book Vertical Proof

- 完成小说 91 或另一冻结长篇的 Chapter→Arc→Global；
- 跑通 Timeline causal、Relationship evolution、Clue payoff；
- 统一工作台和 Reader Chat 的分层消费；
- 完成真实质量、成本和恢复报告。

## Stage 3 — Production Cutover Design

- 新里程碑明确 NM 是否 Promote；
- 设计 Active Pointer、CAS、rollback、compat；
- A/B 对比原有 Narrative Unit/raw retrieval；
- 不达标则保持 candidate-only。

## Stage 4 — Creation Domain

- 建立 Fanfiction 独立知识空间；
- 编辑、版本、设定、续写和导出；
- 原作一致性与创作自由度评测；
- 防止反向污染原作事实层。

---

# 10. 最终判断

NovelMind 不是“架构还没开始”，而是：

> 已建立高级、生产导向的架构骨架，但真实数据、质量证据和跨系统权威尚未完全填满这套骨架。

最接近完成的是 **v0.8 candidate-only 合同目标**。

距离最大的不是继续增加层数，而是：

```text
统一层级定义
+ 存储/投影一致性
+ 整书真实数据闭环
+ 跨章节因果/关系/伏笔语义
+ 当前质量证据
+ 生产消费切换
+ 独立创作知识空间
```

后续任何新功能立项，都应先检查是否依赖本文件中尚未关闭的底层差距。
