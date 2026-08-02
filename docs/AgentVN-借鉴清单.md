# AgentVN 借鉴清单

> 本文档定位：**借鉴文档**——从 AgentVN-Editor 借鉴什么、怎么落地到 NovelMind。
> 不是项目对比评价；不回答"谁更好"，只回答"能拿走什么"。
>
> 来源：AgentVN-Editor @ main（c06df8b，2026-08-01 开源，视觉小说游戏引擎，单人开发）
> 落地基准：NovelMind master（9f01680 快照）+ .planning Phase 26-39 规划（2026-08-02）
> 整理日期：2026-08-02

---

## 一、借鉴源简介（AgentVN 是什么）

- **定位**：免费、零代码、Agent 辅助的视觉小说游戏引擎——把小说文本导入后，经 AI 分析改编成可编辑、可预览、可打包（.vncart）的游戏工程。
- **结构**：`editor/`（React 节点画布编辑器 + Tauri 桌面壳）、`backend/`（FastAPI，约 13k 行 Python）、`GameCli_framework/`（玩家运行时）、`shared/`、`docs/`（22 份格式文档）。
- **技术栈**：极简——fastapi + openai SDK + pydantic + sqlite-vec，仅 8 个 Python 包；AI 全走 OpenAI 兼容协议。
- **核心机制**：小说导入 = "改编"流水线（启发式预分析 → AI 分块扫描 → AI 大纲 → AI 章节规划 → 场景改编）；Agent 批量处理 = 多子代理并行 + 上下文链 + 合并评审；记忆 = 档案 + 客观关系图 + 情感记忆三层。

**为什么值得借鉴**：AgentVN 在 13k 行代码里沉淀了 4 个可复用的工程模式——结构化输出修复、双向上下文链、启发式兜底、诚实元数据（confidence + warnings）。这些与产品形态无关，直接可移植到 NovelMind 的分析/生成管线。

---

## 二、可借鉴清单

### 2.1 可直接移植（代码级，成本 1-2 天以内）

| # | 借鉴项 | AgentVN 出处 | 落地到 NovelMind | 成本 |
|---|---|---|---|---|
| 1 | **说话人/对话正则提取器**：中文姓名 1-6 字 + 说话动词表（说道/问道/低声道…）+ 引号/冒号匹配 | `novel_import_service.py` 的 `SPEAKER_PATTERN` / `DIALOGUE_PATTERN`（约 30 行，纯正则零依赖） | 新建 `services/speech_analysis.py`，挂导入管线或 26 维度适配器，产出"对话/叙述比例、说话人频次" | 半天 |
| 2 | **结构化输出修复层**：pydantic 驱动的 `normalize_structured_payload(model, payload)`，对 LLM 近失误 JSON 做字段别名/缺失定向修复，再进严格校验 | `structured_normalization.py`（核心约 100 行） | 新建 `services/structured_output_fix.py`，接入 `try_llm_enrich` / `llm_judge` / 线索 live re-judge | 1-2 天 |
| 3 | **分块摘要**：每个 chunk 让 LLM 产一行摘要（不进检索索引） | `ai_scan_chunk` 的 `NovelAiChunkSummary` | 导入管线 chunk 阶段加 summary 字段；分析工作台直接获得"章节速览" | 1 天 |
| 4 | **诚实元数据**：每个分析产物统一带 `confidence` + `warnings` | 所有 `NovelAi*` 模型的通用字段 | 各 facet schema 加两个可选字段（成本极低），前端可展示"置信度/警告" | 0.5 天 |

### 2.2 机制借鉴（设计层面，并入现有规划）

| # | 借鉴项 | AgentVN 做法 | 落地到 NovelMind | 落点 |
|---|---|---|---|---|
| 5 | **双向上下文链**：分块处理注入"前文摘要 + 后文提示"，解决分块丢全局叙事 | `_previous_context_summary` + `_next_context_hint`（≤800 字） | NovelMind 已有 `carry_forward`（前向），补"后文提示"这半边 | 并入 28 |
| 6 | **AI 大纲/主线产物**：章节候选 + 大纲结构 + 主线 | `ai_build_outline` → `NovelAiOutlineStructure` / `NovelAiOutlineMainline` | 新增 outline 分析任务，产 candidate-only 大纲，挂知识单元体系 | 并入 27 |
| 7 | **分支建议**：冲突点的可选项（choice_text / branch_summary） | `BranchSuggestion` | 37 冲突检查顺带产出，或独立分析维度 | 并入 37 |
| 8 | **连续性备注**：跨 chunk 衔接说明作为**输出** | `SubagentModelOutput.continuityNotes` | 28 章处理输出侧加 continuity 字段（carry_forward 目前只是输入） | 并入 28 |
| 9 | **启发式兜底**：AI 失败降级正则规则，流程不中断 | `_fallback_scene_candidates` | 26-02 deterministic fallback 的第一级实现用正则规则 | 并入 26 |
| 10 | **全流程流式**：分析/生成全部 SSE，前端实时可见 | `stream_ai_scan_chunk` 等全套 `stream_*` | `analyze/stream` 从 501 补成 SSE | 1-2 天 |
| 11 | **情感记忆**（可选）：客观事实之外的"角色主观情绪状态"，VAD 三维 + 强度衰减 + 漂移 | `EmotionTrace`（valence/arousal/dominance + memory_strength + decay） | 27-02 的 state/goal/motivation/knowledge 补 emotion 维度——**先确认产品定位**（做"角色心理理解"才值） | 并入 27 或 backlog |

### 2.3 已覆盖，无需借鉴

| 借鉴点 | 为什么不需要 |
|---|---|
| 源文本溯源（SourceMapping） | NovelMind 已有 citations + 26-03 EvidenceRef（offset+hash+manifest 冻结+stale 拒绝），更严 |
| 时间线分析 | NovelMind 服务级时间线（样例 ~1933 events + 章范围过滤）远超其 chunk 级笔记 |
| 线索/伏笔 | NovelMind clue 专门模块（plant→payoff + live re-judge），AgentVN 只有近似产物 |
| 任务暂停/恢复/重跑 | 28-01 checkpoint + resume + crash replay + 成本账本已规划 |
| 记忆分层（客观侧） | 27 世界模型 + 认识论标签（canon_fact/推断/解读）概念上超越 |
| 多代理并行 + 合并评审 | 已刻意替换为 28-01 checkpoint 确定性恢复路线（更可控、可审计、可重放）——**不要改回并行 subagent** |
| 素材/演出管理、图片生成 | 33/34/38 视觉规划更强（插画锚定、一致性、命名空间隔离） |

### 2.4 明确不借鉴

- **SQLite 单机存储**——NovelMind 的 PostgreSQL + Alembic + 多用户 owner 隔离是优势。
- **无评测、无安全基线**——AgentVN 没有 EvalDataset 也没有 SSRF/IDOR/密钥加密；NovelMind 都有。
- **单文件超大实现**（1999 行 service）——借鉴设计，不学代码形态。
- **.vncart 游戏打包 / 玩家运行时**——产品形态不同，NovelMind 不做游戏引擎。

---

## 三、落地映射汇总

| 借鉴项 | 落地位置 | 依赖 | 成本 |
|---|---|---|---|
| 说话人/对话提取（#1） | `services/speech_analysis.py` + 26 适配器 | 无 | 半天 |
| 结构化输出修复（#2） | `services/structured_output_fix.py` + 3 处接入点 | 无 | 1-2 天 |
| 分块摘要（#3） | 导入管线 chunk 字段 | 无 | 1 天 |
| 诚实元数据（#4） | 各 facet schema | 无 | 0.5 天 |
| 流式（#10） | `analyze/stream` SSE | 无 | 1-2 天 |
| 大纲/主线（#6） | Phase 27 | 27 世界模型 | 并入 |
| 分支建议（#7） | Phase 37 | 37 冲突检查 | 并入 |
| 后文提示（#5）/连续性（#8） | Phase 28 | 28 章处理 | 并入 |
| 启发式兜底（#9） | Phase 26 | 26-02 | 并入 |
| 情感记忆（#11） | Phase 27 或 backlog | 产品定位确认 | 待定 |

---

## 四、建议执行顺序

1. **第一阶段（独立小改动，随时可做，不依赖 phase gate）**：#1 说话人提取 → #2 结构化输出修复 → #10 流式 → #3 分块摘要 → #4 诚实元数据。全部是 0.5-2 天的小改动，直接验证 NovelMind 现有管线能否吸收这些能力。
2. **第二阶段（并入现有规划，不新增 phase）**：#6 大纲/主线进 27，#5+#8 双向上下文链进 28，#9 兜底进 26，#7 分支进 37——改 plan 内容即可。
3. **第三阶段（待定）**：#11 情感记忆——先回答"NovelMind 要不要做角色心理理解"，再决定进 27 还是 backlog。

---

## 五、附录：AgentVN 分析维度完整清单（30 维）

> 来源：`models/novel_import.py` / `models/novel_process.py` / `novel_import_service.py`。
> 用于持续评估"还有什么值得拿"；"NovelMind 对应"列仅标注是否有对应，不评价优劣。

### A. 文本层（纯启发式，零 AI）
| 维度 | 产出 | NovelMind 对应 |
|---|---|---|
| 说话人提取 | 中文名 1-6 字 + 说话动词表匹配 | 无 ← 可移植（#1） |
| 对话提取/计数 | 引号+冒号匹配，dialogue_count 对话密度 | 无 ← 可移植（#1） |
| 地点关键词 | 内置地点词表 + location_hint | 弱 |
| 文本统计 | char_count / word_count / estimated_tokens | 有（导入时） |

### B. 内容层（AI 分块扫描）
| 维度 | 产出 | NovelMind 对应 |
|---|---|---|
| 分块摘要 | NovelAiChunkSummary（每 chunk 一行） | 无 ← 可移植（#3） |
| 角色索引 | name / aliases / first_seen_offset / description / speaking_style_hint | 有（角色观察），缺 speaking_style 与首现位置 |
| 地点列表 | locations: list[str] | 弱 |
| 时间线笔记 | timeline: list[str]（chunk 级） | 有（服务级） |
| 伏笔/铺垫 | foreshadowing: list[str] | 有（clue plant→payoff） |
| 置信度+警告 | confidence + warnings | 无统一置信度 ← 可移植（#4） |

### C. 结构层（AI 大纲 + 章节规划）
| 维度 | 产出 | NovelMind 对应 |
|---|---|---|
| 章节候选 | start/end_offset、字数、token 估算、anomaly_flags | 有（分割），缺异常标记 |
| 大纲结构 | NovelAiOutlineStructure | 无 ← 可借鉴（#6） |
| 主线 | NovelAiOutlineMainline | 无 ← 可借鉴（#6） |
| 场景候选 | title / location_hint / time_hint / characters / summary / source_excerpt | 有（scene units），缺 time_hint/角色列表 |
| 场景源分段 | 按 max_scene_chars 切源文本段 | 有（hierarchy 精确切片） |

### D. 叙事层（AI 章节规划/改编）
| 维度 | 产出 | NovelMind 对应 |
|---|---|---|
| 冲突点 | conflict_type / mainline_resolution / suggests_branch | 37-03 规划中 |
| 分支建议 | choice_text / branch_summary / enabled_by_default | 无 ← 可借鉴（#7） |
| 连续性备注 | continuityNotes（跨 chunk 衔接说明） | 部分 ← 可借鉴（#8） |

### E. 改编层（AI + 启发式，为游戏服务）
| 维度 | 产出 | NovelMind 对应 |
|---|---|---|
| SceneBeat | VN 命令序列（背景/对话/叙述/立绘/动画） | 无（不做游戏） |
| 素材建议 | background / sprite / bgm / animation + prompt_hint | 33/34 规划（插画锚定） |
| 角色更新 | 改编后 character_updates 增量 | 无 |
| 溯源 | SourceMapping（改编命令→原文偏移） | 有（citations/26-03） |
| 质量警告 | warnings + needs_review | 部分 |

### F. 质量层（Agent 批处理）
| 维度 | 产出 | NovelMind 对应 |
|---|---|---|
| 质量检查 | QualityIssue（code/severity/evidence/action） | 有（评测+一致性） |
| 上下文链 | previousContextSummary + nextContextHint（双向） | 有（carry_forward 单向）← 可借鉴（#5） |
| Token/成本 | input/outputTokens 账本 | 有（usage） |

---

*本文档依据 AgentVN 源码（c06df8b）与 NovelMind 当前代码/规划核实，持续维护。*
