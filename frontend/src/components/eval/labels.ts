// ── Eval UI 常量标签 ──────────────────────────────────────────────

export const TYPE_LABELS: Record<string, string> = {
  original_text: "原文定位",
  character_relation: "人物关系",
  event_causality: "事件因果",
  timeline: "时间线",
  foreshadowing: "伏笔/回收",
};

export const STATUS_LABELS: Record<string, string> = {
  candidate: "候选",
  confirmed: "确认",
  rejected: "驳回",
};

export const DIFFICULTY_LABELS: Record<string, string> = {
  easy: "简单",
  medium: "中等",
  hard: "困难",
};

export const STRATEGY_LABELS: Record<string, string> = {
  bm25: "BM25 全文搜索",
  baseline_vector: "纯向量搜索",
  hybrid_search: "混合搜索",
};
