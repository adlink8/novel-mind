# Dirty WIP Inventory (2026-07-16)

**Purpose:** 分拣工作区未提交改动，**不删除**用户本地 WIP。共约 66 条路径。

## A. 运维 / 本地启动（可保留，建议后续单独 commit）

| Path | 说明 |
|---|---|
| `scripts/keep-alive.ps1` / `start-keep-alive.bat` / `stop-keep-alive.ps1` | 前后端保活 |
| `scripts/keep-alive.pid` | 运行时 PID（通常应 gitignore） |
| `backend/_keep_be.bat`, `start-detached.*`, `start-tunnel.bat` | 本地启动辅助 |
| `frontend/_keep_fe.bat` | 前端保活 |
| `deploy/` | 部署草稿 |

## B. 可能相关产品试验（未纳入 v0.8 提交）

| Path | 说明 |
|---|---|
| `backend/app/services/vertex_gemini.py` | Vertex/Gemini 适配试验 |
| `frontend/src/app/timeline-prototype/` | 时间线原型页 |
| `frontend/.playwright-cli/` | Playwright CLI 缓存/产物（通常应 ignore） |

## C. Reader Chat / Timeline / 分析服务改动（与 v0.8 候选记忆无关）

- `backend/app/api/reader_chat.py`, `services/reader_chat/*`, schemas/prompts/tests  
- `backend/app/api/timeline.py`, `services/timeline/*`, prompts, integration tests  
- `backend/app/services/relationships/worker.py`  
- `backend/app/services/ai_service.py`, `analysis_service.py`, `novel_service.py`  
- `backend/app/api/eval.py`, `novels.py`, `config.py`, `.env.example`  

→ 像是会话中排障 / 侧栏 AI / 分析路径上的本地实验，**不要与 v0.8 混提**。

## D. 前端页面 / API 客户端 / store（可能含 motion 周边或无关改动）

- `frontend/src/app/*`（page, novels, settings, analysis tests）  
- `frontend/src/lib/api.ts`, hooks, stores  
- `frontend/e2e/*`, `playwright.config.ts`, `next.config.mjs`  
- 新增测试：`novel-card.test.tsx`, `progress-bar.test.tsx`, `timeline-chart.test.tsx`  

→ 需 diff 后决定：合并到 motion 后续补丁 / 独立功能分支 / 丢弃。

## Recommended actions (not auto-executed)

1. `scripts/keep-alive.pid`、`frontend/.playwright-cli/` 加入 `.gitignore`（若尚未）  
2. 运维脚本单独 `chore(scripts): keep-alive helpers`  
3. C 组按主题拆 branch 或 `git stash push -m "wip-reader-timeline"`  
4. 勿 `git add -A` 与 v0.8 提交混装  

## Status

- Inventory only; **no files deleted**  
- v0.8 commits remain clean of this WIP  
