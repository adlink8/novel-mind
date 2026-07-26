# Technical Concerns & Known Debt
<!-- rewritten: 2026-07-26; snapshot: master @ 9f01680 -->
<!-- 2026-07-05 版已 superseded：其 C-01/C-02/C-04（Narrative Unit 未实施等）早被 Phase 05–20 交付推翻 -->

> 权威问题清单：[`.planning/AUDIT-STATUS-REFRESH-2026-07-26.md`](../AUDIT-STATUS-REFRESH-2026-07-26.md)
> （2026-07-17 架构审计的逐项现状刷新）。本文件只保留仍然有效的工程关切摘要，按归属 Phase 索引。

## 当前有效关切（按修复归属）

| 关切 | 状态 | 归属 |
|---|---|---|
| 无 Layer Registry/ADR；`L*` 层级命名多义；NU vs NM 边界未定义 | OPEN | Phase 23 |
| raw TextChunk→Chroma 无 journal/幂等/fail-closed；`failed>0` 仍置 novel `ready`；删除窗口残留 | OPEN | Phase 24 |
| 三层检索消费未统一（mode 客户端传入、NM 不在 router、Reader Chat 独立 SOURCE_PRIORITY） | OPEN | Phase 24 |
| clue 无 `short_title`（标题截断 rationale）；clue `cost_usd` 结算恒 0 | OPEN | Phase 25 |
| relationship 无 `intake_kind`/`producer_kind` 来源枚举 | OPEN | Phase 25 |
| characters 占位双轨、`analyze/stream` 501、fanfiction 501 | OPEN | Phase 25（fanfiction → v1.4） |
| `analysis/page.tsx` 两处 setState-in-effect 以 eslint 豁免暂封 | DEBT | Phase 25 |
| Vertex/Gemini 适配无测试/无文档（实验态） | DEBT | Phase 25+ |
| 样例数据差距：NM 117/515 无 Arc/Global；因果边 0；关系演化 0；payoff 0；eval 数据 0 | DATA GAP | Phase 26–28 |
| BM25 中文分词用 `simple`（人名/地名召回弱；zhparser/pg_jieba 备选） | DEBT | v1.2 评测后定 |
| chromadb 1.5.9 PYSEC-2026-311 无修复版（pip-audit 豁免中）；brace-expansion 开发链 advisory（npm audit --omit=dev） | WAIVED | 上游修复后解除，条件见 ci.yml |
| 75MB DB dump 仍在已推送 git 历史（已停止跟踪） | ACCEPTED | 历史重写需显式授权 |
| pytest contract 15s 超时在慢机器上会杀掉 OpenAPI 导出测试（CI 正常，纯本机现象） | LOCAL-ONLY | 可用环境变量放宽时再议 |
| CodeQL Action v3 于 2026-12 弃用 | LOW | 例行维护升 v4 |

## 不应再引用的过期结论

- "narrative_units 表未创建 / promote.py 不存在"（2026-07-05 C-01）→ Phase 05 已交付完整 NU 层。
- "tsconfig 不存在 / TS strict 未开"→ 前端 `tsc --noEmit` 全量通过是 CI Static 门禁的一部分。
- "chromadb==0.4.0 锁定"→ 当前 1.5.9（server digest 锁定 + client 匹配）。
