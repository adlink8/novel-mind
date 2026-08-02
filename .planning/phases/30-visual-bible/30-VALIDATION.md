# Phase 30: Visual Bible - Validation

## Nyquist validation strategy

目标是用最小、可快速运行的测试证明：Visual Bible 是证据链接的 candidate Artifact，且人工 approval 是显式、可审计的状态变化。

### Fixtures

1. `vb-basic-v1`: 一名角色、地点、物品、派系、style profile；每条 canon claim 有同一 source snapshot 的 chapter/range/hash。
2. `vb-interpretation-v1`: 同一实体同时包含 `probable_inference` 与 `user_interpretation`，验证 UI/API 不折叠 authority。
3. `vb-invalid`: 越界 offset、错误 content hash、跨 owner entity、无 evidence 的 canon claim、重复 stable ID。
4. `vb-review`: approve/reject/edit/supersede/needs_relink 事件序列与重复 action。

### Test matrix and commands

|层|验证|命令|
|---|---|---|
|unit|schema、canonical hash、authority gate、review transition|`cd backend; pytest tests/unit/visual_bible -q`|
|integration|owner/version/source-snapshot scope；approval 不改 chapter/active pointer|`cd backend; pytest tests/integration/visual_bible -q`|
|frontend|evidence panel、authority badge、review pending/failed states|`cd frontend; npm test -- visual-bible`|
|browser|桌面与 390px：打开 entity、查看证据、编辑 interpretation、提交 approval|`cd frontend; npm run test:e2e -- visual-bible --project=chromium-desktop --project=chromium-mobile-390`|

### Manual UAT

- 以普通 owner 打开一本小说：只能看到自己的 Visual Bible candidate。
- 查看一个 `canon_fact`：能跳到章节范围，显示 cutoff/hash/source。
- 编辑 `user_interpretation` 后刷新：生成新 revision，旧 revision 仍可读。
- 尝试无证据 approve：服务端拒绝并保留 reason code。
- 检查 generated/reference asset：显示 provenance/rights 状态，未批准不能进入 approved set。

### Failure and stop rules

- 任一跨 owner、错 hash、无证据 canon claim 通过即 fail closed。
- approval 请求重复只返回幂等结果，不创建第二个有效 approval。
- 测试可通过不代表 Phase 22 解除；0/3 Nightly 状态必须保持原样。

## Requirement coverage

`REQ-VIS-01` → contract + evidence + review + browser fixture above。没有 Phase 30 implementation code 时，本文件只定义验证入口，不声称已通过。
