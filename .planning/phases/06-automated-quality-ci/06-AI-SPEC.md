---
phase: 06-automated-quality-ci
spec_version: rag-quality.v1
status: locked
---

# AI Specification: Automated RAG Quality

## Roles and Separation

| Role | Responsibility | Prohibited |
|---|---|---|
| Generator (G) | 从冻结 evidence package 生成 QA case | 评分 SUT、决定 freeze/promote、引用包外证据 |
| Independent Judge (J) | 复核 fixture；盲评 SUT faithfulness/relevance | 与 G 使用相同模型族或相同实际 weights/revision；决定最终资格 |
| SUT | 以当前 RAG pipeline 检索并回答 | 访问 gold answer/claims/J verdict |
| Deterministic validator | hash/offset/schema/claims/equivalence/no-answer 规则 | 以模型意见覆盖失败 |
| Deterministic arbiter | 应用 policy、baseline、health、成本、延迟和统计门 | 在输入缺失时产生 qualified |

G 与 J 的 lineage 必须证明 model family 不同且实际 weights/revision 不同；provider 不同本身不充分，仅别名、temperature、endpoint 或 prompt 不同不算隔离。SUT 可以与 G 或 J 同族，但报告必须披露。

## Canonical Schemas

`SourceSnapshot`: `snapshot_id, owner_id, work_id, version, canonicalization_version, chunks[{content_hash,text_hash,length}], manifest_hash, created_at, signature`。

`EvidenceRef`: `chunk_content_hash, start_offset, end_offset, quote_hash, quote_text?`；载入时重新切片并核验 quote hash。

`EvalCase`: `case_id, schema_version, snapshot_hash, question, case_type(answerable|no_answer|hard_negative), claims[{claim_id,text,critical,evidence_set_ids[]}], equivalent_evidence_sets[{set_id,refs[]}], reference_answer, generator_lineage, judge_fixture_verdict, deterministic_checks, fixture_hash, status`。

`ModelLineage`: `provider, model_family, model_id, weights/revision, endpoint_class, prompt_hash, prompt_version, schema_hash, decoding, runtime, started_at`。

`RunResult`: `run_id, case_id, repetition, retrieved_evidence, answer, parsed_claims, deterministic_metrics, judge_scores, token_usage, cost, latency, dependency_health, status, quality_comparable`。

所有结构化模型输出先以 Pydantic/JSON Schema 验证；raw output 只作审计，不作业务真值。

## Prompt and Model Lineage

- prompt 模板、rubric 与 schema 均版本化并 SHA-256；运行冻结 exact hashes。
- G prompt 强制逐 claim 绑定 evidence set，禁止常识补全；J 收不到 G rationale、SUT model name 或 baseline verdict。
- model alias 必须解析为实际 revision/weights；解析失败为 `invalid_lineage`。
- 同一 fixture 的 regeneration 保留 parent case、attempt 0..2、原因与全部 lineage。

## Fixture Generation and Freeze

`snapshot_ready -> generating -> deterministic_validation -> judge_review -> frozen`。

- deterministic checks：schema、snapshot/hash、offset/quote、claims 非空、critical claim support、equivalent sets、重复/答案泄漏、no-answer 无包内支持、hard-negative 有相近但不支持证据。
- J rubric 每项 0..4：faithfulness、coverage、sufficiency；三项>=3，critical ambiguity=0。
- 失败最多 regenerate 2 次；第 3 次失败进入 `quarantined`，不依赖日常人工确认，也不进入可比较基线。
- freeze 生成 immutable fixture hash 与签名；任何修改形成新版本。

## Evaluation Rubric

- `answer_faithfulness`: 每个 SUT claim 是否由 retrieved evidence 支持；critical unsupported claim rate 必须 0；总体 faithfulness 以重复/案例 bootstrap 95% lower bound >=0.90。
- `answer_relevance`: 回答是否直接解决问题且不夹带无关内容；相对 baseline 回归<=3pp。
- `context_precision`: top-k 中支持任一 gold/equivalent evidence set 的比例。
- `context_recall@5`: 被召回 evidence 覆盖 required claims/equivalent sets 的比例；相对回归<=2pp。
- no-answer：必须拒答且不得制造 claim；hard-negative：不得把近似但不等价证据当支持。
- verdict consistency：同 case 三次运行的资格 verdict 一致率>=0.80。
- cost<=baseline+15%；p95 latency/token/cost 预算从版本化 policy 读取，缺失即 fail closed。

Judge 输出分项分数、claim verdict、evidence refs、critical ambiguity count 与 reason codes；自由文本 rationale 只审计。deterministic arbiter 重新计算全部可确定指标，并检查 J 输出完整性。

## Judge Calibration

- 使用独立冻结、签名 calibration suite，包含 supported、partial、unsupported、contradictory、no-answer、hard-negative 与 equivalent evidence。
- 每个 Judge 实际 weights/revision 上线前运行 3 次；输出按类别混淆矩阵。critical false accept 必须为 0，verdict consistency 必须>=0.80，否则 lineage 状态为 `invalid_lineage`，不得运行 benchmark。
- calibration 与 benchmark 必须使用不同 fixture hash 和不同 domain；启动时确定性校验隔离，禁止用 benchmark 调 prompt、rubric 或阈值。
- calibration report 绑定 suite hash/signature、prompt/schema hash、Judge weights/revision 与运行环境；任一缺失视为 `invalid_lineage`。

## State Machine and Fail-Closed Statuses

运行状态：`queued -> calibrating -> snapshotting -> fixture_generation -> fixture_review -> frozen -> retrieving -> answering -> scoring -> arbitrating -> passed|qualified|quality_regression`。

旁路终态：`blocked_dependency`（live DB/Chroma/model 不可用）、`invalid_fixture`、`invalid_lineage`、`quarantined`、`failed_policy`、`cancelled`。质量阈值回归使用 `failed_policy/quality_regression`，不得使用含混的 `regressed`。只有 `passed/qualified` 可用于 baseline；blocked/invalid/fail/quality_regression 均 `quality_comparable=false` 且所有聚合质量分为 null。

每阶段持久 checkpoint、attempt、lease、heartbeat、input/output hashes；resume 从最后已提交 checkpoint 继续，幂等键为 run+stage+input hash。不得把异常吞成 0。

## Cost and Safety

- 运行前计算 case 数、3 repeats、最大 token 与价格表的 worst-case budget；超过 policy 拒绝启动。
- 每 call 设置 timeout、token ceiling；按 run/model/case 记录 prompt/completion tokens 与成本。
- 到达 hard budget 立即 `failed_policy`；禁止自动换成未记录的廉价模型继续产生可比较结果。
- evidence/prompt 视为不可信内容：结构化边界、长度限制、指令隔离、输出 schema 与 quote verification 防 prompt injection。
- adversarial fixtures 必须覆盖 instruction injection、超长输入、schema smuggling、malicious quote/offset 与 cross-owner evidence；任何突破或无法验证结果为 `invalid_fixture` 或 `failed_policy`，metrics 必须为 null。

## Offline / Online Split

- PR offline：固定 snapshot/fixture、fake deterministic model transcripts、真实 PostgreSQL/Chroma 与 Chromium；不读取 secrets，不产生 live quality baseline。
- main：完整 integration 与兼容 API，允许受保护环境显式启用 live smoke；无依赖则 blocked，不阻塞 secretless correctness gate的报告生成。
- nightly/self-hosted online：strict Ollama/配置的 G/J、三次 benchmark、成本/延迟/漂移、baseline comparison 与告警；只有该层可产生 live comparable qualification。

## Compatibility

旧 Eval API 通过 adapter 读取新 job/report，同时保留旧字段。旧 `gold_chunks` 仅用于 legacy display/migration；资格门拒绝仅有 DB ID 的 case。deprecation metadata 包含替代 endpoint/schema 与迁移截止条件；删除旧契约需要独立后续决策。

## Plan Allocation

- `06-03` 实现 snapshot、fixture、adversarial validation、G/J lineage separation 与独立 Judge calibration；不承担 SUT run。
- `06-04` 只消费已签名且校准通过的 fixture/lineage，实现 SUT retrieval/answer、四项质量指标、policy arbiter、durable run/resume、legacy adapter 与 live dual-model test。
- `06-06` 执行 nightly benchmark并生产签名报告；`06-07` 只聚合报告、验证 release gate 与远端 branch protection，不重新解释模型分数。
