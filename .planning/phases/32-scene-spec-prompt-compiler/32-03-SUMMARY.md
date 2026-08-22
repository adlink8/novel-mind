# 32-03 SUMMARY — Provider Prompt Adapters

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 32 override)

## What Was Built

1. **`backend/app/services/prompt_compiler/adapters.py`** — `PromptAdapter` protocol
   （mock-provider + prompt-blocks.v1）、`compile_prompt` 纯函数、`PromptArtifact`、
   `PromptRevisionService`（compile/preview/create/edit/diff/list/load）。
2. **`backend/app/services/prompt_compiler/serialization.py`** — 可重放序列化、
   `diff_prompt_revisions`、`edited_spec_with_interpretation`（仅 user_interpretation 可编辑、
   no-op 拒绝）。
3. **`backend/app/api/prompt_revisions.py`** — 5 端点（list/get/preview/create/edit/diff）。
4. **`backend/app/main.py`**（修改）— 注册 prompt_revisions router。
5. **测试**：`test_adapters.py` 14 + `test_golden.py` 17。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/prompt_compiler -q`（两次） | ✅ 31 passed / 31 passed |
| `pytest tests/unit/prompt_compiler/test_adapters.py tests/unit/prompt_compiler/test_golden.py -q` | ✅ **31 passed** |
| `pytest tests/unit/scene_spec -q` | ✅ **58 passed**（回归） |
| `pytest tests/unit -q`（全量） | ✅ **875 passed** |
| `pytest tests/integration/scene_spec -q` | ✅ **8 passed** |
| `alembic heads` | ✅ 单 head `20260801_scene_spec_prompt`（无新 migration） |
| `from app.main import app` | ✅ OK |

## 关键设计

- provider-neutral → provider-specific PromptArtifact adapter 契约；
- golden 测试确定性可重放；edit 仅 user_interpretation 可编辑、no-op 拒绝；
- 无 unsupported detail 伪装成 canon；candidate-only。

## 备注 / 偏差

- `main.py` 注册为必要附加改动（API 需可达）。
- edit 需新 `prompt_key`（DB 唯一约束使同 key 无法存两个修订）；revision_number = 父+1、
  `parent_prompt_revision_id` 链接。
- input_hash 非跨 adapter 相等（`prompt_input_payload` 含每 adapter 独立的 config_hash）；
  canonical sections/negative/uncertainties 仍相同，canonical meaning 不变。
- Phase 32 全部切片（01 契约/02 编译器/03 适配器）已交付；Phase 33 可直接消费
  `FrozenPromptRevisionView` 作为图像生成 job 输入。
