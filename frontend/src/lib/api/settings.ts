/**
 * 用户设置 API。
 */

import { api } from "./client";

/** 智能路由策略偏好 */
export type RoutingPreference = "quality" | "balanced" | "budget";

export interface RoutingPreferenceResponse {
  preference: RoutingPreference;
}

export const settingsApi = {
  getRouting: () => api.get<RoutingPreferenceResponse>("/settings/routing"),
  putRouting: (preference: RoutingPreference) =>
    api.put<RoutingPreferenceResponse>("/settings/routing", { preference }),
};
