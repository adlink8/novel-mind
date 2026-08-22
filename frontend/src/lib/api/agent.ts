/**
 * 智能体运行时 API（25.2-03/04 Agent Workspace）。
 */

import { api } from "./client";

/** 技能运行状态（与后端 SkillRunView.status 对齐）。 */
export type SkillRunStatus =
  | "queued"
  | "running"
  | "cancelled"
  | "completed"
  | "failed";

/** 技能运行行（后端 SkillRunView 字段子集）。 */
export interface SkillRunView {
  id: number;
  owner_id: number;
  novel_id: number;
  skill_version_id: number;
  status: SkillRunStatus;
  status_reason: string | null;
  stop_reason: string | null;
  branch: string | null;
  input_hash: string;
  error_code: string | null;
  cancel_requested: boolean;
  retry_count: number;
  created_at: string;
  updated_at: string;
}

/** 产物状态（后端 ArtifactView.status 前向状态机）。 */
export type ArtifactStatus =
  | "candidate"
  | "validated"
  | "approved"
  | "published"
  | "rejected";

/** Cited Answer 答案块引证（D-10 信封 content.answer.answer_blocks）。 */
export interface CitedAnswerBlockCitation {
  chapter_id: number;
  source_start: number;
  source_end: number;
  evidence_key: string;
  block_id?: string;
  context_evidence_ref_id?: number;
}

export interface CitedAnswerBlock {
  text?: string;
  citations?: CitedAnswerBlockCitation[];
}

export interface CitedAnswerContent {
  type?: string;
  schema_version?: string;
  answer?: { answer_blocks?: CitedAnswerBlock[] };
}

/** 产物行（后端 ArtifactView；content 为客户端扩展，取自已读 revision）。 */
export interface ArtifactView {
  id: number;
  owner_id?: number;
  novel_id?: number;
  skill_version_id?: number;
  run_id?: number;
  branch?: string | null;
  type: string;
  schema_version: string;
  status: ArtifactStatus;
  current_revision_id?: number | null;
  created_at?: string;
  updated_at?: string;
  content?: CitedAnswerContent;
}

/** 后端统一分页信封 {"items","total","skip","limit"}。 */
export interface AgentPaginated<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}

export const agentApi = {
  /**
   * 取某小说最新一次技能运行（session restore 状态源；无则 null）。
   * SSE 运行端点由 agent-service 内部创建 run，前端只读回最新 run 作为状态。
   */
  getLatestRun: async (
    novelId: string | number
  ): Promise<SkillRunView | null> => {
    const res = await api.get<AgentPaginated<SkillRunView>>(
      `/agent/novels/${novelId}/skill-runs`,
      { params: { skip: 0, limit: 1 } }
    );
    return res.data.items[0] ?? null;
  },

  /** 取消技能运行（流内取消以 abort fetch 为主，本端点作兜底）。 */
  cancelRun: (novelId: string | number, runId: number | string) =>
    api.post<SkillRunView>(
      `/agent/novels/${novelId}/skill-runs/${runId}/cancel`
    ),

  /** 取某小说最新产物（无则 null）。 */
  getLatestArtifact: async (
    novelId: string | number
  ): Promise<ArtifactView | null> => {
    const res = await api.get<AgentPaginated<ArtifactView>>(
      `/agent/novels/${novelId}/artifacts`,
      { params: { skip: 0, limit: 1 } }
    );
    return res.data.items[0] ?? null;
  },

  /** 按 id 读单个产物。 */
  getArtifact: async (novelId: string | number, artifactId: number | string) => {
    const res = await api.get<ArtifactView>(
      `/agent/novels/${novelId}/artifacts/${artifactId}`
    );
    return res.data;
  },

  /** 读产物最新修订正文（CitedAnswerContent，引证芯片数据源）。 */
  getArtifactContent: async (
    novelId: string | number,
    artifactId: number | string
  ): Promise<CitedAnswerContent | null> => {
    const res = await api.get<AgentPaginated<{ content?: CitedAnswerContent }>>(
      `/agent/novels/${novelId}/artifacts/${artifactId}/revisions`,
      { params: { skip: 0, limit: 50 } }
    );
    const items = res.data.items;
    return items.length > 0
      ? (items[items.length - 1].content ?? null)
      : null;
  },

  /** 批准产物：candidate→validated→approved→published（唯一前向状态路径）。 */
  approveArtifact: async (artifactId: number | string) => {
    const res = await api.post<ArtifactView>(
      `/agent/artifacts/${artifactId}/approve`
    );
    return res.data;
  },

  /** 拒绝产物：candidate/validated → rejected。 */
  rejectArtifact: async (artifactId: number | string) => {
    const res = await api.post<ArtifactView>(
      `/agent/artifacts/${artifactId}/reject`
    );
    return res.data;
  },
};
