---
phase: 07-semantic-hierarchical-chunking
spec_version: semantic-chunker.v1
status: locked-for-planning
system_type: Hybrid (structured boundary classification + RAG ingestion)
framework: Existing FastAPI + LiteLLM >=1.83.10 + Pydantic >=2.13
model_provider: Model-agnostic through LiteLLM; initial local profile Ollama qwen3.5:9b
---

# AI Specification: Semantic and Hierarchical Chunking

## 1. System Classification and Non-Negotiable Boundaries

本系统不是文本生成器或 autonomous agent。规则系统拥有 source snapshot、候选边界、offset、层级组装、持久化、索引、发布和回滚；LLM 仅对低置信相邻 span 做结构化分类。

| 角色 | 允许 | 禁止 |
|---|---|---|
| Rule chunker | 规范化、atomic span、confidence/reason、硬边界和 fallback | 把启发式 confidence 宣称为概率 |
| LLM adjudicator | `split|merge|abstain`、选择输入内 context-preserve span、reason code | 生成/改写正文或事实；调用工具/DB；决定 candidate/promotion |
| Deterministic validator | schema、enum、offset/hash、coverage、层级和预算验证 | 修补模型语义或接受越界输出 |
| Build/publish service | immutable candidate、manifest、reconcile、pointer journal | 原地覆盖 active；绕过 Phase 06 |
| Phase 06 arbiter | A/B 质量、成本、延迟、lineage 的最终资格判断 | 缺输入时给可比较分数 |

Critical failures：原文丢失/重复、跨 owner/novel/chapter、LLM 造文、offset/hash 不一致、树断裂、低质量 candidate 成为 active、旧 pointer 丢失、故障被记成 0 分或成功。

## 2. Framework Decision

保留现有 FastAPI/SQLAlchemy/PostgreSQL/Chroma 服务边界，使用 LiteLLM 的 `acompletion` 和 Pydantic strict schema。不要添加 LangChain、LlamaIndex、LangGraph 或 agent SDK：该流程是线性、少工具、强审计的分类与发布管线，新增编排框架只会复制 Phase 05/06 已有状态机和 lineage。

初始模型 profile：`ollama/qwen3.5:9b`，`temperature=0`，`max_tokens=180`，单请求 timeout 20s，初次失败后最多重试 1 次。model alias 必须解析/记录实际 revision；无法解析则只能构建 fallback candidate，不能把该运行用作新的模型质量 baseline。provider 可替换，但必须形成新 lineage 并重新 qualification。

## 3. Framework Quick Reference

### 安装

项目已有依赖；Phase 07 不新增包：

```powershell
pip install "litellm>=1.83.10" "pydantic>=2.13" "fastapi>=0.115"
```

### 关键 imports 与最小入口

```python
from typing import Literal

from litellm import acompletion
from pydantic import BaseModel, ConfigDict, Field


class BoundaryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    boundary_id: str
    decision: Literal["split", "merge", "abstain"]
    preserve_left_span_ids: list[str] = Field(default_factory=list, max_length=3)
    preserve_right_span_ids: list[str] = Field(default_factory=list, max_length=3)
    reason_code: Literal[
        "SCENE_CONTINUES", "TIME_SHIFT", "LOCATION_SHIFT", "SPEAKER_SHIFT",
        "POV_SHIFT", "COREFERENCE_RISK", "DIALOGUE_CONTINUES", "INSUFFICIENT_CONTEXT",
    ]


async def adjudicate_boundary(messages: list[dict[str, str]]) -> BoundaryDecision:
    response = await acompletion(
        model="ollama/qwen3.5:9b",
        messages=messages,
        response_format=BoundaryDecision,
        temperature=0,
        max_tokens=180,
        timeout=20,
        num_retries=1,
        stream=False,
    )
    # Provider enforcement is not trusted by itself; validate locally again.
    return BoundaryDecision.model_validate_json(
        response.choices[0].message.content
    )
```

### 核心抽象

| 抽象 | Phase 07 用途 |
|---|---|
| `acompletion` | 异步、受 timeout/token/retry 限制的低置信边界调用 |
| Pydantic `response_format` | 向支持 provider 下发 JSON Schema；本地仍二次验证 |
| `BoundaryProposal` | 规则边界、confidence、reason codes、相邻 span hashes |
| `BoundaryDecision` | LLM 唯一允许的业务输出；不含任意正文或持久化字段 |
| `ChunkerManifest` | source/rule/prompt/schema/model/decision/node lineage 与 checksum |

### 特定坑点

1. `response_format` 支持在 provider 间不等价；Ollama/Groq 等历史上出现 Pydantic 转换或 JSON 后处理问题。因此 provider 声称支持不等于业务有效，必须本地 `model_validate_json`。
2. 不对结构化边界使用 streaming。LiteLLM 的结构化 streaming parse 没有统一稳定契约；这里必须 await 完整响应后一次验证。
3. `num_retries` 可能产生重复调用与费用；每个边界必须用 `build_id + boundary_id + prompt_hash` 幂等审计，最大总尝试固定为 2。
4. LiteLLM/provider 的 cost 和 usage 曾在 streaming、reasoning tokens、自定义价格上不一致；保存原始 usage、计算值和版本化价格表，缺价格时按 token/resource budget fail closed。
5. 不把 fallback model 配进透明自动路由。换模型会改变语义分布；只有显式新 lineage 和重新 qualification 才可比较。

### 建议目录（实施阶段）

```text
backend/app/services/chunking/
  rules.py          # atomic spans, boundary confidence/reasons
  schemas.py        # strict Pydantic contracts
  adjudicator.py    # bounded LiteLLM call + retry/fallback
  hierarchy.py      # chapter -> scene -> evidence assembly
  manifests.py      # hashes, offsets, lineage, checksum
  builds.py         # immutable full/incremental candidates
  promotion.py      # Phase 05-style prepare/commit pointer
  rollback.py       # pointer rollback + reconcile
  eval.py           # Phase 06 adapter and A/B metrics
```

### Sources

- https://docs.litellm.ai/docs/completion/json_mode
- https://docs.litellm.ai/docs/completion/stream
- https://docs.litellm.ai/docs/completion/reliable_completions
- https://docs.litellm.ai/docs/exception_mapping
- https://docs.litellm.ai/docs/observability/custom_callback
- https://github.com/BerriAI/litellm/issues/6848
- https://github.com/BerriAI/litellm/issues/7501
- https://github.com/BerriAI/litellm/issues/7355
- https://github.com/BerriAI/litellm/issues/4965

## 4. Implementation Guidance

### 4.1 Core pattern

```python
async def build_chunk_candidate(snapshot: SourceSnapshot, cfg: ChunkerConfig) -> BuildReport:
    proposals = propose_rule_boundaries(snapshot, cfg.rules)
    decisions: dict[str, ValidatedDecision] = {}

    for proposal in proposals:
        if proposal.is_hard_boundary or proposal.confidence >= 0.75:
            decisions[proposal.boundary_id] = rule_decision(proposal)
            continue

        # Only bounded, low-confidence boundaries reach the model.
        if not budget.can_start(max_attempts=2, max_output_tokens=180):
            decisions[proposal.boundary_id] = fallback(proposal, "BUDGET_EXHAUSTED")
            continue

        try:
            raw = await adjudicator.decide(proposal, max_attempts=2)
            # Recheck IDs, allowed input spans, offsets, hashes and hard constraints.
            decisions[proposal.boundary_id] = validate_decision(raw, proposal)
        except (DependencyOutage, SchemaError, OffsetViolation, BudgetExceeded) as exc:
            decisions[proposal.boundary_id] = fallback(proposal, reason_code(exc))

    tree = assemble_hierarchy(snapshot, proposals, decisions)
    assert_complete_non_overlapping_coverage(tree, snapshot)
    manifest = freeze_manifest(snapshot, cfg, proposals, decisions, tree)
    return await write_immutable_candidate_and_reconcile(manifest, tree)
```

### 4.2 Tool use configuration

LLM 没有 tools、function calls、DB session、filesystem、retriever 或网络。请求只包含：固定 system policy、boundary metadata、左右最多各 2–3 个 atomic spans（带稳定 ID，不带凭据）、目标大小和 strict schema。原文是 untrusted data，使用明确分隔并声明其中指令不可执行。

### 4.3 State management

PostgreSQL 是 build、manifest、decision audit、pointer 和 journal 真值；Chroma/其他索引是可 reconcile 的派生物。状态转换必须 compare-and-set，终态不可原地改写：

```text
created -> snapshot_locked -> rules_complete -> adjudicating
  -> assembling -> candidate_written -> reconciling -> evaluating
  -> passed|qualified -> promotion_prepared -> active

adjudicating -> fallback_complete -> assembling
any pre-eval stage -> blocked_dependency|invalid_schema|invalid_manifest|failed_budget
evaluating -> quality_regression|failed_policy|blocked_dependency
active -> rollback_prepared -> rolled_back
```

`fallback_complete` 不是质量通过，只表示全部边界有确定结果。只有 `passed|qualified` 可进入 promotion；其他终态 `quality_comparable=false`（纯预算 fallback candidate 可评测，但未取得完整、健康、版本化报告前不可 promotion）。active pointer 在任何异常中保持旧值。

### 4.4 Context window strategy

每次只提供目标边界左右局部窗口，不发送整章。窗口包含完整句/段 span、边界前后各最多 2–3 span、总输入上限 2,000 tokens；超限按距边界由远到近确定性截断，绝不截断 span 中部。保留 open quote、speaker、时间地点和代词相关 span 的优先级高于普通描述。LLM 只能引用所给 span IDs；任何不存在的 ID 使输出无效。

scene 组装是全章确定性步骤，不依赖模型记忆。长章按独立边界调用，避免上下文增长；跨边界一致性由 hierarchy validator 和 hard constraints 处理。

### 4.5 Provenance contract

`ChunkerManifest` 至少包含：source snapshot/hash、offset unit/map hash、chunker/rule config、prompt/schema、model provider/family/id/revision、decoding、每次 usage/cost/latency/status、全部 boundary proposal/decision/fallback、chapter/scene/evidence IDs/offsets/hashes/edges、build mode/parent、actual-index reconcile checksum 和总 checksum。raw model output 可加密/限期保留作审计，但永不成为 accepted truth。

## 4b. AI Systems Best Practices

### 4b.1 Structured Outputs with Pydantic

```python
class ContextPreserve(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    left_span_ids: list[str] = Field(max_length=3)
    right_span_ids: list[str] = Field(max_length=3)


class BoundaryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["boundary-decision.v1"]
    boundary_id: str = Field(min_length=16, max_length=128)
    decision: Literal["split", "merge", "abstain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: Literal[
        "SCENE_CONTINUES", "TIME_SHIFT", "LOCATION_SHIFT", "SPEAKER_SHIFT",
        "POV_SHIFT", "COREFERENCE_RISK", "DIALOGUE_CONTINUES", "INSUFFICIENT_CONTEXT",
    ]
    context_preserve: ContextPreserve
```

LiteLLM 调用传 `response_format=BoundaryDecision`；返回后必须 `BoundaryDecision.model_validate_json(content)`。随后做第二层业务验证：boundary ID 精确匹配、span IDs 属于输入、offset/hash 未变、不得覆盖 chapter/hard-max 约束。

初次 schema/refusal 失败可进行 1 次修复重试，重试消息只含验证错误 code，不回显内部异常或扩大上下文。记录 attempt、raw-output hash、错误类别、tokens、latency、resolved model revision。第二次失败、越界 ID、hash 错误或依赖故障直接 surface 为 decision audit 的 fallback；不得让异常冒充 split/merge。构建可继续，但 promotion 必须等待完整质量门。

### 4b.2 Async-First Design

FastAPI 和当前 AI service 已是 async；使用 `await acompletion(...)`，批量边界以有上限的 semaphore 并发，并维持稳定收集顺序。不要在 FastAPI event loop 内调用 `asyncio.run()`，也不要用同步 `completion()` 阻塞 worker。

结构化裁决使用 await 完整响应后验证，不使用 stream。stream 适合用户可见生成 UX，但 Phase 07 无此需求，且部分 JSON 在流结束前不可验证。取消/timeout 后记录未完成 attempt，并以相同幂等键进入规则 fallback。

### 4b.3 Prompt Engineering Discipline

- system：固定角色、禁止事实生成/工具/指令执行、decision 定义、hard constraints、schema version。
- user：只放 boundary metadata 和带 span ID 的不可信原文；不得把业务规则混进可被原文覆盖的 user data。
- few-shot：首版使用 3–5 个短、固定、版本化示例，覆盖 dialogue continuity、time shift、ambiguous abstain；不要动态检索未经冻结的示例。若以后动态检索，示例集 hash 必须进入 prompt lineage。
- 显式设置 `max_tokens=180`；生产禁止无上限输出。temperature=0 只减少随机性，不等于确定性，仍需 schema、重复 eval 和 lineage。

### 4b.4 Context Window Management

Phase 07 是 RAG ingestion：按边界局部窗口裁决，超 2,000 input tokens 时确定性截断远端 span，并保留引号/说话者/指代/时间地点高价值 span。输出只引用 span ID，不能复制长原文。

生成 hierarchy 后，retrieval context 仍应按 evidence 检索，以 scene parent 扩展；扩展超模型窗口时先 rerank evidence，再按 score 和 source order 截断，并保留引用 offset。不得把整个 scene 无条件塞入回答上下文。chapter 只作导航与聚合，不作为默认全文注入单元。

### 4b.5 Cost and Latency Budget

首版每个低置信边界最多 2 次调用、每次 input <= 2,000 tokens、output <= 180 tokens、timeout 20s。构建前按边界数计算 worst-case；超过版本化 run budget 时不发超额调用，剩余边界规则 fallback。总调用并发、总 tokens、p95 latency、fallback 原因和每章分布均进入 manifest/report。

以 10,000 个规则候选边界、20% 低置信为例，上限为 2,000 × 2 attempts × (2,000+180) = 8.72M tokens；这是 hard worst case，不是目标。目标应通过规则置信筛选、一次成功和短窗口压到 <= 2.5M tokens。货币估算使用运行时锁定价格表：`input_tokens × input_price + output_tokens × output_price`；本地 Ollama 货币值记 0 时仍对 tokens、20s timeout、并发和 wall-clock 设门。

exact-match cache key：`model_revision + prompt_hash + schema_hash + left/right span hashes + rule_config_hash`。只缓存已通过 schema/offset 验证的决定；semantic cache 禁止用于边界裁决，因为相似文本不保证相同人物、时间或叙事边界。较便宜模型只可作为显式、重新评测的 profile，用于低风险分类；不得在 outage 时透明替换。

## 5. Locked Output Contracts

除 `BoundaryDecision` 外，实施必须定义 strict Pydantic：

- `AtomicSpan`: stable ID、chapter、source/normalized offsets、text hashes、span type。
- `BoundaryProposal`: adjacent IDs、rule decision、confidence、reason codes、hard constraints、input hash。
- `DecisionAudit`: source `rule|llm|fallback`、attempts、raw hash、validated output、usage/cost/latency、fallback reason、lineage。
- `HierarchyNode`: `chapter|scene|evidence`、parent、ordered children、source range(s)、content hash、chunk type。
- `ChunkerManifest`: build/source/config/prompt/schema/model lineage、nodes/edges/decisions、reconcile 和 checksum。
- `ChunkerQualityReport`: A/B IDs、fixture/policy/baseline hashes、metrics、fallback breakdown、status、`quality_comparable`、signature。

全部 `extra="forbid"`；业务 enum 不允许自由文本替代。LLM schema 中不提供 `content`、SQL、tool call、publish 或 active pointer 字段。

## 6. Evaluation and Release Gates

| Dimension | Method | Gate |
|---|---|---|
| Schema/lineage | deterministic | 100% valid；缺失即 invalid |
| Source coverage | offset sweep/hash | 100% cover、0 overlap/duplication、0 cross-chapter |
| Tree integrity | deterministic | 每 evidence 恰有一个 scene parent；无 orphan/cycle |
| Boundary quality | human gold code metric | split F1 >=0.90；B 不低于 A；critical false split=0 |
| Scene coherence | calibrated human/judge rubric | mean >=4/5；critical fail=0 |
| Retrieval | Phase 06 frozen A/B | Recall@5 regression <=2pp；MRR/NDCG regression <=3pp |
| Faithfulness | Phase 06 | critical unsupported claim rate=0；其余沿 policy |
| Reproducibility | double-build checksum | 相同输入/config/transcripts checksum 相同 |
| Fallback | deterministic report | outage/schema/budget 分项完整；不得静默 |
| Cost/latency | policy arbiter | total/p95 within policy；cost <= A +15% |
| Promotion safety | journal/reconcile | stale pointer、checksum mismatch、non-qualified 全阻断 |

只有 `passed|qualified` 且 manifest/reconcile/signature 完整的 B 可 prepare promotion。任何 missing policy/baseline/health/lineage、blocked、invalid 或 regression 都保留 A。首个真实 cutover 保留 Phase 05 的显式 operator approval；之后是否自动 promotion 由现有发布政策决定，不由 LLM 决定。

## 7. Test Strategy (Future Implementation; No Tests Run in This Research Phase)

1. **Unit/code**：段落/句子 offsets、confidence/reason、hard boundaries、coverage、tree invariants、stable IDs、manifest checksum、预算计算和状态迁移。
2. **Schema/transcript contract**：合法 JSON、extra field、错误 enum、错误 boundary/span ID、malformed JSON、refusal、timeout、rate limit、重复响应；断言最多 1 次 retry 后 deterministic fallback。
3. **Adversarial**：正文中的 system/tool/SQL 指令、schema smuggling、超长 span、Unicode/CRLF、重复句、未闭合引号、跨 owner IDs；必须 0 越权和 0 hash 漂移。
4. **Property/metamorphic**：拼接全部 evidence 等于 source canonical slice；相同输入重复构建同 checksum；只改一章只改变 affected lineage；规则次序稳定。
5. **Integration**：PostgreSQL immutable candidate、Chroma exact-ID reconcile、并发 prepare/commit CAS、failure leaves active unchanged、incremental carry-forward/deletion、rollback/restore。
6. **Eval**：10–20 个高质量章节起步，`split|merge|acceptable_either` 金标；A/B boundary、hierarchy、retrieval、faithfulness、fallback、成本和延迟。dev 与 frozen 隔离。
7. **Live/nightly only**：真实 `ollama/qwen3.5:9b` structured output 和 outage；PR 使用冻结 transcripts/fake store，不运行模型，不把 live blocked 伪装成 0 分。

## 8. Locked AI Risks and Guardrails

- LLM 无写库/索引/发布能力，无 tools；输出不含事实或正文。
- 所有模型决定绑定 source span IDs、offsets、hashes、prompt/schema/model revision。
- 规则 hard constraints 可否决 LLM；LLM 不可跨 chapter 或突破 hard max。
- provider/schema/outage/budget 全有确定性 fallback，且 fallback 不代表 qualified。
- raw output 只用于审计，经过最小化、hash/脱敏和保留期策略；不得记录密钥或完整不必要正文。
- exact cache 仅复用已验证同 lineage 决定；禁止 semantic cache。
- immutable candidate + active pointer + prepare/commit + reconcile + rollback 是唯一发布路径。
- Phase 06 deterministic arbiter 是唯一质量资格决策者；LLM judge 不决定发布。

## Checklist

- [x] 现有框架、真实 imports 和 async structured-output pattern 已锁定
- [x] LLM 权限边界、strict schema、retry/fallback 已锁定
- [x] chapter → scene → evidence、offset/manifest/version lineage 已锁定
- [x] candidate A/B、active pointer、增量重切、promotion/rollback 已锁定
- [x] 状态机、质量门、成本安全和失败语义已锁定
- [x] eval、adversarial、contract、integration、live test 策略已锁定
