# 03-01 实施摘要

日期: 2026-06-13
状态: **PARTIAL** — 实现切片已落地，2026-06-13 验证发现质量验收缺口

## Slice 1: 评测数据结构 + ORM ✓

- 新增 ORM 模型: `EvalDataset`, `EvalRun`, `EvalResult` (app/models/eval.py)
- 新增 Pydantic Schema: 7 个请求/响应模型 (app/schemas/eval.py)
- Alembic 迁移: `518675fa18f8_create_eval_tables.py`
- Novel 模型新增 `eval_datasets` / `eval_runs` 关系
- 后端测试: 6 个模型测试全部通过
- 样本数据: `evals/novel_eval_candidates_sample.json`

## Slice 2: 候选测试题生成器 ✓

- 脚本: `scripts/generate_eval_candidates.py`
- 支持 5 种题型: original_text, character_relation, event_causality, timeline, foreshadowing
- 使用 ai_service.chat() 生成，prompt 模板化
- 测试: 8 个 prompt 构建 + 响应解析测试全部通过

## Slice 3: 自动评测引擎 ✓

- 服务: `app/services/eval_service.py`
  - 支持 2 种策略: baseline_vector（纯向量）, hybrid_search（BM25+向量融合）
  - 指标: recall@k, precision@k, MRR, NDCG@k
  - 异步逐题评测 + 错误案例标记
  - 延迟导入外部服务避免测试环境 ChromaDB 连接
- API: `app/api/eval.py`
  - POST /api/eval/runs — 触发评测
  - GET /api/eval/runs/{id} — 获取报告
  - GET /api/eval/datasets — 数据列表
  - PATCH /api/eval/datasets/{id} — 人工审核
- CLI: `scripts/run_rag_eval.py`
  - JSON 结果 + Markdown 报告自动生成
- 测试: 12 个指标计算测试全部通过
- 路由注册: main.py 挂载 eval.router

## Slice 4: 前端评测管理页 ✓

- 页面: `frontend/src/app/eval/page.tsx`
  - 3 个 Tab: 评测数据集 / 评测运行 / 结果对比
  - 数据集: 按类型/状态筛选, 人工确认/驳回
  - 运行: 创建 Run 表单, 历史列表, 报告详情 + 错误案例
  - 对比: 策略 A vs B 的指标并排展示
- 侧边栏: 新增 "RAG 评测" 入口 (layout.tsx)
- 前端构建: Next.js 16 Turbopack build 通过
- 前端测试: 22 Vitest 全部通过

## Slice 5: 文档与 GSD 收尾 ✓

- IMPLEMENTATION-STATUS.md: 新增 RAG 评测条目, 测试数更新
- ROADMAP.md: 初次实现时标记 Complete；2026-06-13 复审后改为 Gaps Found
- 03-01-SUMMARY.md: 本文档

## 验证

```bash
# Backend
cd backend
alembic upgrade head && alembic check  ✓
pytest tests/ -v -m "not e2e"  # 239 passed  ✓

# Frontend
cd frontend
npm test  # 22 passed  ✓
npm run build  # passed  ✓
npm run lint  ✓
```

## 复审结论

- VERIFIED：ORM/迁移、候选生成、三种检索策略、指标计算、错误案例、API、CLI、前端管理与可视化。
- FIXED：评测 API owner 隔离、NDCG 算法、损坏 JSON/导入脚本、ORM/Alembic 漂移。
- GAPS：数据库仅 10/100 confirmed；6 次真实运行质量指标全为 0；faithfulness/cost 未计算；HTTP 触发仍同步执行。
- 最终状态以 `03-01-VERIFICATION.md` 和 `.planning/v0.3-MILESTONE-AUDIT.md` 为准。
