/**
 * 同人文 API。
 */

import { api } from "./client";

export interface FanFiction {
  id: string;
  novel_id: string;
  title: string;
  description: string;
  style_config: Record<string, unknown>;
  branch_point: Record<string, unknown>;
  status: "draft" | "writing" | "completed";
}

export const fanfictionApi = {
  list: (novelId: string) => api.get<FanFiction[]>(`/fanfiction/${novelId}`),
  create: (data: Partial<FanFiction>) => api.post<FanFiction>("/fanfiction", data),
  continueWriting: (fanfictionId: string, prompt: string) =>
    api.post(`/fanfiction/${fanfictionId}/continue`, { prompt }),
};
