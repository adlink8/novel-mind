# Phase 35 Context: PWA 基础设施与可安装性

## Phase Goal
让现有 Next.js 前端可安装、app-shell 可离线加载——v1.5 PWA 的地基。后端零改动。

## Requirements
REQ-MOBILE-01（P0）：PWA 可安装——manifest 完整、添加到主屏幕可用、Lighthouse installable 通过。

## Depends on
无（基于现有 frontend；后端不动）。

## Documentation inputs（规划已读）
- `.planning/ROADMAP.md` Phase 35 段
- `.planning/milestones/v1.5-REQUIREMENTS.md`（REQ-MOBILE-01、范围边界、红线）
- `.planning/config.json`（task_plan_required_sections: Steps/Must-Haves/Verification）
- `frontend/package.json`（Next 16.3.0-canary.6、app router、react 19）
- `frontend/src/app/` 结构（app router 布局入口）

## Documentation outputs（执行阶段需更新）
- `.planning/STATE.md`（Phase 35 进度）
- `.planning/ROADMAP.md`（Phase 35 plans 勾选）
- `frontend/README.md` 或 `docs/`（PWA 构建/离线说明，Phase 39 收口，本阶段先记入口）

## 环境约定（来自项目记忆，执行时必须遵守）
- Frontend build 须 `env -u NODE_OPTIONS npm run build`（Next 16 build worker 拒绝注入的 NODE_OPTIONs）。
- 生产预览 `npm start -- -p 3010` + `BACKEND_URL=http://127.0.0.1:8000`（backend 实际在 8000，非 8010）。
- 后端零改动：`backend/` 无 diff，无新增 API，契约测试不变。

## Waves
1 (35-01 ∥ 35-02) → 2 (35-03)

## 红线
- 不做原生 App / 不上架 / 不推送（v1.5 范围外）。
- 不改后端。
- 不做 NM promotion / active pointer / Reader Chat cutover（沿用既有红线）。
