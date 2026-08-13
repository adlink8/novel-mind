import { api } from "./client";
import type { Novel, NovelListResponse } from "./novels";

export type ToolCategory = "read" | "candidate" | "action";
export type SkillStatus = "draft" | "active" | "deprecated";

export interface ToolCapability {
  name: string;
  category: ToolCategory;
  approval_required: boolean;
  user_configurable: boolean;
}

export interface ToolCatalogResponse {
  items: ToolCapability[];
  total: number;
  http_tools: "not_enabled";
  execution_boundary: "builtin_declarative_only";
}

export type ToolConnectorStatus = "draft" | "validated" | "active" | "disabled";
export type ToolConnectorMethod = "GET" | "POST";

export interface ToolConnector {
  id: number;
  connector_id: number;
  version_id: number;
  version: number;
  owner_id: number;
  name: string;
  description?: string | null;
  base_url: string;
  path: string;
  method: ToolConnectorMethod;
  request_schema: Record<string, unknown>;
  response_schema: Record<string, unknown>;
  enabled: boolean;
  status: ToolConnectorStatus;
  created_at: string;
}

export type ToolConnectorPayload = Omit<
  ToolConnector,
  "id" | "connector_id" | "version_id" | "version" | "owner_id" | "status" | "created_at"
>;

export interface SkillRegistryItem {
  id: number;
  owner_id: number;
  novel_id: number;
  name: string;
  description?: string | null;
  status: SkillStatus;
  created_at?: string;
  updated_at?: string;
}

export interface SkillVersion {
  id: number;
  registry_id: number;
  owner_id: number;
  novel_id: number;
  name: string;
  version: string;
  description?: string | null;
  prompt: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  allowed_tools: string[];
  budget: Record<string, unknown>;
  status: SkillStatus;
  execution_status: "declarative_only";
  runtime_note: string;
}

export interface SkillRegisterPayload {
  novel_id: number;
  name: string;
  version: string;
  description: string;
  prompt: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  allowed_tools: string[];
  budget: Record<string, unknown>;
}

interface Page<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}

export const extensionsApi = {
  listNovels: () => api.get<NovelListResponse>("/novels"),
  listSkills: () => api.get<Page<SkillRegistryItem>>("/agent/skills"),
  listSkillVersions: (skillName: string) =>
    api.get<Page<SkillVersion>>(`/agent/skills/${encodeURIComponent(skillName)}/versions`),
  getToolCatalog: () => api.get<ToolCatalogResponse>("/agent/tools/catalog"),
  listToolConnectors: () => api.get<{ items: ToolConnector[]; total: number }>("/extensions/tools"),
  createToolConnector: (payload: ToolConnectorPayload) =>
    api.post<ToolConnector>("/extensions/tools", payload),
  updateToolConnector: (id: number, payload: ToolConnectorPayload) =>
    api.put<ToolConnector>(`/extensions/tools/${id}`, payload),
  validateToolConnector: (id: number) =>
    api.post<ToolConnector>(`/extensions/tools/${id}/validate`),
  updateToolConnectorStatus: (id: number, status: "active" | "disabled") =>
    api.patch<ToolConnector>(`/extensions/tools/${id}/status`, { status }),
  dryRunToolConnector: (id: number, request: Record<string, unknown>) =>
    api.post<{ status_code: number; headers: Record<string, string>; body: unknown }>(
      `/extensions/tools/${id}/dry-run`,
      { request },
    ),
  createSkill: (payload: SkillRegisterPayload) =>
    api.post<SkillVersion>("/agent/skills", payload),
  updateSkillVersionStatus: (
    skillName: string,
    versionId: number,
    status: SkillStatus,
  ) =>
    api.patch<SkillVersion>(
      `/agent/skills/${encodeURIComponent(skillName)}/versions/${versionId}`,
      { status },
    ),
};

export type { Novel };
