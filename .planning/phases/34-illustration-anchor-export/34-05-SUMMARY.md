# 34-05 SUMMARY — propose-illustration-anchor Skill 集成 + 确定性发布

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 34 override)

## What Was Built

1. **`agent-service/src/skills/propose-illustration-anchor/`** — 版本化 branch-aware Skill
   资产（schemas、允许工具 allowlist 含 publish_illustration/attach_illustration_to_text
   action、budget、permissions、approval actions）。
2. **工具注册 14→16**：publish_illustration / attach_illustration_to_text 两个 action 工具
   （创建候选 proposal + pending ApprovalRequest）。
3. **`backend/app/services/illustration_anchors/publish.py`** — `create_anchor_proposal`
   候选 gate + `publish_anchor` 确定性发布器 + `build_anchor_manifest`。
4. **`backend/app/api/illustration_anchors.py`** — proposals 创建/读取、publish、manifest、
   anchors 读取。
5. **backend wire + integrity**：IllustrationAnchorProposalArtifact + `illustration_anchor_
   proposal` integrity 分支。
6. **测试**：`propose-illustration-anchor.test.ts` 62 + `test_phase_34_skill.py` 14 +
   `test_publish.py` 15。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **776 passed / 20 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_34_skill.py tests/integration/illustration_anchors/test_publish.py -q`（两次） | ✅ 29 passed / 29 passed |
| `pytest tests/integration/agent_runtime -q` | ✅ **202 passed** |
| `pytest tests/unit -q`（全量） | ✅ **964 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- Agent 输出 candidate-only 直到确定性校验 + Web Approval 完成；
- 只有已批准、已验证、branch-scoped 修订能到达确定性发布（29p 对抗覆盖：伪造/过期/取消/
  拒绝审批、payload hash 篡改、stale 修订、wrong scope）；
- Agent Service 不能直接写 Original Canon、domain tables、published state；FastAPI 拥有
  state，确定性 publisher 拥有 approved publication。

## 备注 / 偏差

- `published_asset_revision_id` 复用 proposal-ready AssetRevision 本身（不新插行），
  manifest 冻结 asset ref 锁死二进制。
- 两个 action 工具语义：都创建完整候选 proposal + pending ApprovalRequest，仅 approval
  action 不同；文本绑定即 proposal 的精确 source span。
- authority_space 服务端派生（derivative = branch+fork 都在）；真正防线在 finalize 的
  `envelope.branch == run.branch` 与 approval/publish scope gate（与 Phase 33 同模式）。
- ApprovalRequest 血缘：run_id/skill_version_id 经工具 params 传入，Artifact 记录 proposal
  refs，ApprovalRequest 绑定 run/skill/novel + payload_hash（D-15 可重放）。
