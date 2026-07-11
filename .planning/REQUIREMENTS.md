# Requirements: 安全与架构修复

## Requirements Status

| ID | Requirement | Priority | Status |
|---|---|---|---|
| REQ-AUDIT-01 | 以 VERIFIED / PARTIAL / MISSING 建立实际实现基线 | P0 | VERIFIED |
| REQ-AUDIT-02 | 修复启动级前后端契约 | P0 | VERIFIED |
| REQ-AUDIT-03 | 未实现核心端点不得伪装成功 | P0 | VERIFIED |
| REQ-AUDIT-04 | 建立可重复 smoke/test/build 命令 | P0 | VERIFIED |
| REQ-AUDIT-05 | 文档状态与代码现实一致 | P0 | VERIFIED |
| REQ-SEC-01 | Git 忽略敏感配置、依赖、构建和上传数据 | P0 | VERIFIED |
| REQ-SEC-02 | 上传文件限制在根目录并具备失败补偿 | P0 | VERIFIED |
| REQ-SEC-03 | API 建立身份和资源授权边界 | P0 | VERIFIED |
| REQ-SEC-04 | 自定义模型 URL 阻断 SSRF，错误对外脱敏 | P0 | VERIFIED |
| REQ-SEC-05 | provider key 加密存储并支持旧密钥轮换 | P1 | VERIFIED |
| REQ-DATA-01 | ORM/Alembic 对齐并在 PostgreSQL 执行检查 | P0 | VERIFIED |
| REQ-ARCH-01 | reader 动态路由和章节响应模型正确 | P1 | VERIFIED |
| REQ-ARCH-02 | 导入进度使用持久化、并发安全的 job 模型 | P1 | VERIFIED |
| REQ-CI-01 | CI 覆盖默认分支，检查非交互且依赖风险受控 | P1 | VERIFIED |
| REQ-RAG-01 | 小说上传后可完成分块、embedding 与 Chroma 索引 | P0 | VERIFIED |
| REQ-RAG-02 | 混合搜索返回 owner 隔离的章节、chunk、score 与 evidence | P0 | VERIFIED |
| REQ-RAG-03 | 前端提供全局搜索与阅读页内搜索入口 | P1 | VERIFIED |
| REQ-EVAL-01 | 评测数据/API/CLI/可视化基础设施可重复运行 | P1 | VERIFIED |
| REQ-EVAL-02 | 形成 100 条人工确认的高质量评测题 | P1 | MISSING |
| REQ-EVAL-03 | 产出有效 baseline/hybrid 质量基线及完整指标 | P1 | PARTIAL |
| REQ-KG-01 | LLM 只做语义分析和判断，脚本负责候选召回、证据包、规则、阈值、状态机和写库 | P1 | VERIFIED |
| REQ-KG-02 | 所有候选、判断、接受关系和图边必须可追溯到真实 evidence refs | P1 | VERIFIED |
| REQ-KG-03 | 向量/BM25/邻近关系只能作为候选召回信号，不能直接写成图事实 | P1 | VERIFIED |
| REQ-KG-04 | 同一图谱链路支持小说与历史两类语料，通过 domain/ontology profile 区分 | P1 | VERIFIED |
| REQ-KG-05 | PostgreSQL 是候选、判断、证据和接受状态的事实源；Neo4j 仅作为可重放投影 | P2 | VERIFIED |
| REQ-KG-06 | 图谱链路具备 fixture/eval/成本/延迟/faithfulness 验证，不依赖人工凭感觉验收 | P1 | VERIFIED |
| REQ-NU-01 | accepted judgments 只能经版本化 draft/canonical 流水线生成 narrative units，禁止在 gate 中直接发布 | P0 | MISSING |
| REQ-NU-02 | 每个 narrative unit 必须保留 owner、work、domain、source judgment 和真实 evidence refs 的完整 lineage | P0 | MISSING |
| REQ-NU-03 | canonicalization 显式处理重复、冲突、时态与 deprecated 状态，hard-negative 错误合并为零 | P0 | MISSING |
| REQ-NU-04 | Chroma candidate collection 不可变且可重建，PostgreSQL 保存 build、版本、pointer 和发布审计事实 | P0 | MISSING |
| REQ-NU-05 | 检索支持 units、chunks 和混合模式；默认切换前 raw chunks 始终作为 fallback | P1 | MISSING |
| REQ-NU-06 | candidate 必须通过 fiction/history frozen A/B、faithfulness、延迟和 canary 门禁才能 promote | P0 | MISSING |
| REQ-NU-07 | promotion 使用 prepare/commit journal，失败可联合回滚 DB、collection、pointer 和 manifest | P0 | MISSING |
| REQ-NU-08 | 增量刷新只处理受影响 evidence/subjects，删除与失效零残留，无变化时 LLM/index 写入为零 | P1 | MISSING |

## Traceability

| Requirement | Plan | Evidence |
|---|---|---|
| REQ-SEC-01, REQ-SEC-02 | 02-01 | `.gitignore` checks; upload/delete rollback tests |
| REQ-SEC-03..05 | 02-02 | auth/ownership/SSRF/crypto tests |
| REQ-DATA-01, REQ-ARCH-01, REQ-CI-01 | 02-03 completed slices | PostgreSQL migration, Next build, CI and audits |
| REQ-ARCH-02 | 02-03 completed slice | ImportJob table、租约并发、幂等、取消和重启恢复测试 |
| REQ-RAG-01..03 | v0.3 core/search/frontend | 12 RAG e2e；hybrid search tests；前端 lint/test/build |
| REQ-EVAL-01 | 03-01 | `03-01-VERIFICATION.md`；eval API owner regression tests |
| REQ-EVAL-02..03 | 03-01 | 当前 10 confirmed / 90 candidate；6 次运行检索指标为 0 |
| REQ-KG-01..06 | 04-01..04-04 completed | `.planning/phases/04-llm/04-*-SUMMARY.md`; `backend/tests/test_knowledge_eval.py`; knowledge graph gate/projection tests |
| REQ-NU-01..08 | 05-01..05-05 planned | `.planning/phases/05-narrative-knowledge-unit-layer/05-*-PLAN.md` |

## Current Evidence

- 236 backend tests and 22 frontend tests pass.
- pip-audit and npm audit report zero known vulnerabilities.
- Bandit medium/high, Ruff and ESLint report zero findings.
- PostgreSQL `upgrade/current/check` passes at `518675fa18f8`.
- Import progress is persisted through `ImportJob`; lease control, retry, cancellation and restart recovery are verified.
- Phase 04 knowledge graph fixture eval has 20 labeled fiction/history examples and deterministic offline tests.
- `backend/scripts/run_knowledge_graph_eval.py` reports recall signal quality separately from accepted graph fact quality, with cost/latency fields present.
