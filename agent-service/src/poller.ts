/**
 * queued-run poller（Phase 40 / chat_backfill）。
 *
 * 轮询 FastAPI 的 queued chat_backfill 运行，claim 后用现有会话/执行链路
 * 跑一次分析 skill，finalize 落 artifact，随后 FastAPI 侧 materializer 把
 * 产物物化到域表 candidate。
 *
 * 安全契约：
 * - 全程 pull（绝不 FastAPI→agent-service）；
 * - 只 claim origin='chat_backfill' 的 run（不碰前端 SSE 的 user_sse run）；
 * - claim 原子性 + lease 过期 reclaim（后端 409/lease 兜底）；
 * - 执行失败 → 通知 cancel/finalize(failed)，run 终态以 FastAPI 为准。
 */

import { config } from "./config.js";
import { createSession } from "./agent/session-factory.js";
import type { LoadedSkill } from "./skills/loader.js";
import type { ToolRegistryEntry } from "./governance/tool-registry-manifest.js";
import {
  buildCitedAnswerEnvelope,
  type RunLineageContext,
  type ToolEvidence,
} from "./structured-output/cited-answer-builder.js";
import {
  buildAnalysisEnvelope,
  isAnalysisSkill,
} from "./structured-output/analysis-envelope-builder.js";

/** poller 依赖（与 server.ts resolveDeps 对齐）。 */
export interface PollerDeps {
  fetchImpl?: typeof fetch;
  loadSkillImpl?: (name: string) => LoadedSkill;
  validateRunOutputImpl?: (skill: LoadedSkill, output: unknown) => void;
  createSessionImpl?: typeof createSession;
}

interface QueuedRunItem {
  run_id: number;
  owner_id: number;
  novel_id: number;
  skill_version_id: number;
  input: Record<string, unknown>;
  input_hash: string;
  branch: string | null;
  backfill_dimension: string | null;
}

interface ClaimedRun extends QueuedRunItem {
  skill_name: string | null;
  internal_token: string;
  frozen_manifest: Record<string, unknown>;
  budget_snapshot: Record<string, unknown>;
}

interface LastMessage {
  stopReason?: string;
  provider?: string;
  model?: string;
  usage?: unknown;
  text?: string;
}

function lastAssistantMessage(messages: readonly unknown[]): LastMessage | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as { role?: string; stopReason?: string } | undefined;
    if (m?.role === "assistant" && m.stopReason) {
      const content = (m as { content?: unknown[] }).content ?? [];
      const text = content
        .filter((b) => (b as { type?: string }).type === "text")
        .map((b) => (b as { text?: string }).text ?? "")
        .join("");
      return {
        stopReason: m.stopReason,
        provider: (m as { provider?: string }).provider,
        model: (m as { model?: string }).model,
        usage: (m as { usage?: unknown }).usage,
        text,
      };
    }
  }
  return undefined;
}

function extractToolEvidences(messages: readonly unknown[]): ToolEvidence[] {
  const out: ToolEvidence[] = [];
  for (const m of messages) {
    const msg = m as {
      role?: string;
      toolName?: string;
      isError?: boolean;
      content?: unknown[];
    } | undefined;
    if (msg?.role !== "toolResult" || msg.isError) continue;
    const text = (msg.content ?? [])
      .filter((b) => (b as { type?: string }).type === "text")
      .map((b) => (b as { text?: string }).text ?? "")
      .join("")
      .slice(0, 2000);
    if (msg.toolName && text) {
      out.push({ toolName: msg.toolName, content: text });
    }
  }
  return out;
}

function resolvePollerDeps(deps: PollerDeps = {}) {
  return {
    fetchImpl: deps.fetchImpl ?? (globalThis.fetch as typeof fetch),
    loadSkillImpl:
      deps.loadSkillImpl ??
      (() => {
        throw new Error("loadSkillImpl not provided");
      }),
    validateRunOutputImpl:
      deps.validateRunOutputImpl ?? (() => {
        /* 输出校验可选；后端 finalize 权威兜底。 */
      }),
    createSessionImpl: deps.createSessionImpl ?? createSession,
  };
}

function serviceHeaders(): Record<string, string> {
  return {
    authorization: `Bearer ${config.novelmindGatewayToken}`,
    "content-type": "application/json",
  };
}

async function fetchQueuedRuns(
  fetchImpl: typeof fetch,
): Promise<QueuedRunItem[]> {
  const res = await fetchImpl(
    `${config.fastApiBaseUrl}/api/agent/queued-runs`,
    { headers: serviceHeaders() },
  );
  if (!res.ok) {
    throw new Error(`queued-runs HTTP ${res.status}`);
  }
  const body = (await res.json()) as { items?: QueuedRunItem[] };
  return body.items ?? [];
}

async function claimRun(
  fetchImpl: typeof fetch,
  runId: number,
): Promise<ClaimedRun> {
  const res = await fetchImpl(
    `${config.fastApiBaseUrl}/api/agent/queued-runs/${runId}/claim`,
    { method: "POST", headers: serviceHeaders() },
  );
  if (res.status === 409) {
    throw new ClaimConflictError();
  }
  if (!res.ok) {
    throw new Error(`claim HTTP ${res.status}`);
  }
  return (await res.json()) as ClaimedRun;
}

class ClaimConflictError extends Error {}

/** 执行一次 backfill run（session.prompt + finalize）。 */
async function executeRun(
  deps: ReturnType<typeof resolvePollerDeps>,
  manifest: ToolRegistryEntry[],
  claimed: ClaimedRun,
): Promise<void> {
  const skillName = claimed.skill_name ?? "answer-reading-question";
  const question = String(
    (claimed.input ?? {}).question ?? (claimed.input ?? {}).query ?? "",
  ).trim();
  if (!question) {
    throw new Error("backfill run input.question missing");
  }
  const skill = deps.loadSkillImpl(skillName);

  const runLineage: RunLineageContext = {
    runId: String(claimed.run_id),
    ownerId: claimed.owner_id,
    novelId: claimed.novel_id,
    skillVersionId: claimed.skill_version_id,
    inputHash: claimed.input_hash,
  };

  const session = await deps.createSessionImpl({
    auth: `Bearer ${claimed.internal_token}`,
    novelId: claimed.novel_id,
    skill,
    manifest,
  });

  try {
    await session.prompt(question);
    const last = lastAssistantMessage(session.messages as unknown[]);
    const stopReason = last?.stopReason ?? "error";
    if (stopReason !== "stop") {
      throw new Error(`backfill run stopReason=${stopReason}`);
    }

    let envelopePayload: Record<string, unknown>;
    let frozenManifest: Record<string, unknown> | undefined;
    if (skillName === "answer-reading-question") {
      const evidences = extractToolEvidences(session.messages as unknown[]);
      const built = buildCitedAnswerEnvelope(last?.text ?? "", runLineage, skill, evidences);
      envelopePayload = built.envelope;
      frozenManifest = built.frozenManifest;
    } else if (isAnalysisSkill(skillName)) {
      // 分析 skill：模型输出是结构化 JSON，按 skill 构造对应 envelope.type
      // （scene_candidate / world_model_candidate / visual_bible）。解析失败或
      // 无 leaf 证据 → 抛错（诚实失败，绝不伪造 cited_answer 信封）。
      if (!last?.text) {
        throw new Error(`backfill run: skill ${skillName} returned no model output`);
      }
      const built = buildAnalysisEnvelope(
        last.text,
        runLineage,
        skill,
        claimed.branch ?? null,
      );
      envelopePayload = built.envelope;
      frozenManifest = built.frozenManifest;
    } else {
      // 未接线的 skill：诚实失败（不伪造 cited_answer envelope 导致假 artifact）。
      throw new Error(`backfill run: no envelope builder for skill ${skillName}`);
    }

    const finalizeRes = await deps.fetchImpl(
      `${config.fastApiBaseUrl}/api/agent/novels/${claimed.novel_id}/skill-runs/${claimed.run_id}/finalize`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${claimed.internal_token}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          stop_reason: "stop",
          envelope: envelopePayload,
          model_lineage:
            last?.provider && last?.model
              ? { provider: last.provider, model: last.model }
              : {},
          source_versions: {},
          usage: last?.usage ?? {},
          ...(frozenManifest ? { frozen_manifest: frozenManifest } : {}),
        }),
      },
    );
    if (!finalizeRes.ok) {
      throw new Error(`finalize HTTP ${finalizeRes.status}`);
    }
  } finally {
    await session.abort().catch(() => undefined);
  }
}

async function notifyCancel(
  fetchImpl: typeof fetch,
  novelId: number,
  runId: number,
  internalToken: string,
): Promise<void> {
  try {
    await fetchImpl(
      `${config.fastApiBaseUrl}/api/agent/novels/${novelId}/skill-runs/${runId}/cancel`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${internalToken}`,
          "content-type": "application/json",
        },
      },
    );
  } catch {
    /* 尽力而为 */
  }
}

/**
 * 创建 queued-run poller。
 * start() 返回 stop 函数；内部定时轮询 + 并发上限。
 */
export function createPoller(
  depsInput: PollerDeps = {},
  manifest: ToolRegistryEntry[] = [],
  opts?: { intervalMs?: number; concurrency?: number },
): { start: () => () => void } {
  const deps = resolvePollerDeps(depsInput);
  const intervalMs = opts?.intervalMs ?? config.pollIntervalMs;
  const concurrency = opts?.concurrency ?? config.pollConcurrency;
  let timer: ReturnType<typeof setInterval> | null = null;
  let stopping = false;

  async function tick(): Promise<void> {
    if (stopping) return;
    let items: QueuedRunItem[];
    try {
      items = await fetchQueuedRuns(deps.fetchImpl);
    } catch {
      return; // 轮询失败静默重试
    }
    const slotCount = Math.min(items.length, concurrency);
    await Promise.all(
      items.slice(0, slotCount).map(async (item) => {
        let internalToken: string | null = null;
        try {
          const claimed = await claimRun(deps.fetchImpl, item.run_id);
          internalToken = claimed.internal_token;
          await executeRun(deps, manifest, claimed);
        } catch (err) {
          if (err instanceof ClaimConflictError) return;
          // 执行失败：通知 cancel（cancel-no-write），run 终态以 FastAPI 为准。
          if (internalToken) {
            await notifyCancel(
              deps.fetchImpl,
              item.novel_id,
              item.run_id,
              internalToken,
            );
          }
        }
      }),
    );
  }

  return {
    start: () => {
      void tick();
      timer = setInterval(() => void tick(), intervalMs);
      return () => {
        stopping = true;
        if (timer) clearInterval(timer);
      };
    },
  };
}
