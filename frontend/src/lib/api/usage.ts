/**
 * 用量统计 API。
 */

import { api } from "./client";

export interface UsageSummary {
  today_cost_usd: number;
  week_cost_usd: number;
  month_cost_usd: number;
  total_tokens: number;
}

export const usageApi = {
  summary: () => api.get<UsageSummary>("/usage/summary"),
};
