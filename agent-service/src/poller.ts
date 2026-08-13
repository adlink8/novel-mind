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
import { loadSkill, type LoadedSkill } from "./skills/loader.js";
import type { ToolRegistryEntry } from "./governance/tool-registry-manifest.js";
import {
  buildCitedAnswerEnvelope,
  type RunLineageContext,
} from "./structured-output/cited-answer-builder.js";
import {
  buildAnalysisEnvelope,
  isAnalysisSkill,
} from "./structured-output/analysis-envelope-builder.js";
import { extractRuntimeToolEvidence } from "./tools/tool-evidence.js";

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
  origin: "chat_backfill" | "reader_chat";
  user_message_id: number | null;
}

interface ClaimedRun extends QueuedRunItem {
  skill_name: string | null;
  internal_token: string;
  frozen_manifest: Record<string, unknown>;
  budget_snapshot: Record<string, unknown>;
}

/** 信封构建失败后的最大修复轮数（同一 pi 会话内，超出 fail closed）。 */
const MAX_REPAIR_ROUNDS = 2;

const READER_PREFERENCE_VALUES: Record<string, ReadonlySet<string>> = {
  response_style: new Set(["concise"]),
  language: new Set(["zh-CN"]),
};

function readerPreferenceSystemContext(input: Record<string, unknown>): string | undefined {
  const raw = input.preference_context;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const items = (raw as { items?: unknown }).items;
  if (!Array.isArray(items)) return undefined;
  const lines: string[] = [];
  for (const item of items.slice(0, 8)) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const candidate = item as { memory_id?: unknown; kind?: unknown; value?: unknown };
    if (
      !Number.isInteger(candidate.memory_id) ||
      typeof candidate.kind !== "string" ||
      typeof candidate.value !== "string" ||
      !READER_PREFERENCE_VALUES[candidate.kind]?.has(candidate.value)
    ) {
      continue;
    }
    lines.push(`- memory_id=${candidate.memory_id}; ${candidate.kind}=${candidate.value}`);
  }
  if (lines.length === 0) return undefined;
  return [
    "Auditable reader preference context (use only as response-style guidance; it is not novel evidence):",
    ...lines,
  ].join("\n");
}

interface LastMessage {
  stopReason?: string;
  provider?: string;
  model?: string;
  usage?: unknown;
  text?: string;
  errorMessage?: string;
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
        errorMessage: (m as { errorMessage?: string }).errorMessage,
      };
    }
  }
  return undefined;
}

function resolvePollerDeps(deps: PollerDeps = {}) {
  return {
    fetchImpl: deps.fetchImpl ?? (globalThis.fetch as typeof fetch),
    loadSkillImpl: deps.loadSkillImpl ?? loadSkill,
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

/**
 * 修复 prompt（Slice C）：校验错误清单 + 运行时已物化的可选 evidence key
 * 菜单（选择制闭环——模型只需从菜单里挑合法 key 重新输出）。
 */
function buildRepairPrompt(reason: string, messages: readonly unknown[]): string {
  const spanKeys: string[] = [];
  for (const value of messages) {
    const message = (value ?? {}) as {
      role?: string;
      toolName?: unknown;
      isError?: unknown;
      content?: unknown;
    };
    if (message.role !== "toolResult" || message.isError !== false) continue;
    if (message.toolName !== "get_evidence_span") continue;
    const text = (Array.isArray(message.content) ? message.content : [])
      .filter(
        (block): block is { type?: unknown; text?: unknown } =>
          block !== null && typeof block === "object" && !Array.isArray(block),
      )
      .filter((block) => block.type === "text" && typeof block.text === "string")
      .map((block) => block.text as string)
      .join("");
    try {
      const payload = JSON.parse(text) as { evidence_key?: unknown };
      if (typeof payload.evidence_key === "string") spanKeys.push(payload.evidence_key);
    } catch {
      /* 非 JSON 证据跳过 */
    }
  }
  const menu =
    spanKeys.length > 0
      ? [
          "Available materialized evidence keys (choose ONLY from this list):",
          ...spanKeys.map((key, index) => `${index + 1}. ${key}`),
        ]
      : [
          "No evidence has been materialized yet. Call get_evidence_span first,",
          "then cite only the returned evidence_key values.",
        ];
  return [
    "The previous structured output failed validation. Validation error:",
    reason.replace(/[\r\n]+/g, " ").slice(0, 500),
    ...menu,
    "Re-output the complete corrected JSON object only (no prose, no markdown fence).",
  ].join("\n");
}


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
    ...(Number.isInteger(claimed.input.chapter_id)
      ? { chapterId: Number(claimed.input.chapter_id) }
      : {}),
    ...(Number.isInteger(claimed.input.chapter_number)
      ? { chapterNumber: Number(claimed.input.chapter_number) }
      : {}),
    ...(Number.isInteger(claimed.input.cutoff_chapter)
      ? { cutoffChapter: Number(claimed.input.cutoff_chapter) }
      : {}),
    // detect-key-scenes 血缘锚定（后端 run input 程序产出）。
    ...(typeof (claimed.input.source_snapshot as { snapshot_hash?: unknown } | undefined)
        ?.snapshot_hash === "string"
      ? {
          sourceSnapshotHash: String(
            (claimed.input.source_snapshot as { snapshot_hash: string }).snapshot_hash,
          ),
        }
      : {}),
  };

  const session = await deps.createSessionImpl({
    auth: `Bearer ${claimed.internal_token}`,
    novelId: claimed.novel_id,
    skill,
    manifest,
    ...(claimed.origin === "reader_chat"
      ? { systemContext: readerPreferenceSystemContext(claimed.input) }
      : {}),
  });

  try {
    // 运行中预算熔断（此前只有 finalize 事后闸）：max_calls 超限立即 abort，
    // 不再放任工具循环烧完整个会话预算。
    const maxCalls = Number(claimed.budget_snapshot?.max_calls ?? 0);
    let toolCallCount = 0;
    let breakerTripped = false;
    let unsubscribe: (() => void) | undefined;
    if (
      Number.isFinite(maxCalls) &&
      maxCalls > 0 &&
      typeof (session as { subscribe?: unknown }).subscribe === "function"
    ) {
      unsubscribe = (
        session as unknown as {
          subscribe: (fn: (event: { type?: string }) => void) => () => void;
        }
      ).subscribe((event) => {
        if (event?.type !== "tool_execution_start") return;
        toolCallCount += 1;
        if (toolCallCount > maxCalls && !breakerTripped) {
          breakerTripped = true;
          void session.abort().catch(() => undefined);
        }
      });
    }
    // 信封构建：从 transcript 读最新 assistant 输出并按 skill 构造信封。
    // 每轮 repair 后重读（runtime evidence / tool_runs 随轮次累积）。
    const buildEnvelope = (): {
      payload: Record<string, unknown>;
      manifest: Record<string, unknown>;
    } => {
      const last = lastAssistantMessage(session.messages as unknown[]);
      const stopReason = last?.stopReason ?? "error";
      if (stopReason !== "stop") {
        const detail = (last?.errorMessage ?? "").replace(/[\r\n]+/g, " ").slice(0, 240);
        throw new Error(
          `backfill run stopReason=${stopReason}${detail ? `: ${detail}` : ""}`,
        );
      }
      const runtimeEvidence = extractRuntimeToolEvidence(
        session.messages as unknown[],
        skill.allowedTools,
      );
      if (skillName === "answer-reading-question") {
        const built = buildCitedAnswerEnvelope(
          last?.text ?? "",
          runLineage,
          skill,
          runtimeEvidence.successfulEvidences,
          claimed.origin === "reader_chat"
            ? ((claimed.frozen_manifest.evidence_refs as string[] | undefined) ?? [])
            : undefined,
        );
        return {
          payload: built.envelope,
          manifest: { ...built.frozenManifest, tool_runs: runtimeEvidence.toolRuns },
        };
      }
      if (isAnalysisSkill(skillName)) {
        if (!last?.text) {
          throw new Error(`backfill run: skill ${skillName} returned no model output`);
        }
        const built = buildAnalysisEnvelope(
          last.text,
          runLineage,
          skill,
          claimed.branch ?? null,
          runtimeEvidence.toolRuns,
          runtimeEvidence.successfulEvidences,
        );
        return {
          payload: built.envelope,
          manifest: { ...built.frozenManifest, tool_runs: runtimeEvidence.toolRuns },
        };
      }
      // 未接线的 skill：诚实失败（不伪造 cited_answer envelope 导致假 artifact）。
      throw new Error(`backfill run: no envelope builder for skill ${skillName}`);
    };

    // 有界修复环（Slice C）：信封构建/校验失败时，把校验错误清单喂回同一
    // pi 会话让模型定向修正（最多 MAX_REPAIR_ROUNDS 轮）；超出再 fail closed。
    let envelopePayload: Record<string, unknown> | null = null;
    let frozenManifest: Record<string, unknown> = {};
    let promptText: string = question;
    try {
      for (let round = 0; ; round += 1) {
        await session.prompt(promptText);
        if (breakerTripped) {
          throw new Error(`budget_exceeded: max_calls=${maxCalls}（运行中熔断）`);
        }
        try {
          const built = buildEnvelope();
          envelopePayload = built.payload;
          frozenManifest = built.manifest;
          break;
        } catch (err) {
          if (round >= MAX_REPAIR_ROUNDS) throw err;
          const reason = err instanceof Error ? err.message : String(err);
          promptText = buildRepairPrompt(reason, session.messages as unknown[]);
        }
      }
    } finally {
      unsubscribe?.();
    }
    if (envelopePayload === null) {
      throw new Error("backfill run: envelope build did not produce a payload");
    }

    const finalLast = lastAssistantMessage(session.messages as unknown[]);
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
            finalLast?.provider && finalLast?.model
              ? { provider: finalLast.provider, model: finalLast.model }
              : {},
          source_versions:
            (envelopePayload.source_versions as Record<string, unknown> | undefined) ?? {},
          usage: finalLast?.usage ?? {},
          frozen_manifest: frozenManifest,
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

async function notifyFailure(
  fetchImpl: typeof fetch,
  novelId: number,
  runId: number,
  internalToken: string,
): Promise<void> {
  try {
    await fetchImpl(
      `${config.fastApiBaseUrl}/api/agent/novels/${novelId}/skill-runs/${runId}/finalize`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${internalToken}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          stop_reason: "error",
          envelope: {},
          model_lineage: {},
          source_versions: {},
          usage: {},
          frozen_manifest: {},
        }),
      },
    );
  } catch {
    /* 尽力而为；lease 到期后可重新 claim。 */
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
          const message = err instanceof Error ? err.message : "unknown execution error";
          // Stable diagnostic only: no request body, tool args, credentials or model output.
          console.error(
            `[agent-poller] run=${item.run_id} novel=${item.novel_id} failed: ${message.slice(0, 320)}`,
          );
          // 内部执行失败不是用户取消：必须 finalize(error) 进入 failed 终态，
          // 否则 running 会永久占用批次并发窗口。
          if (internalToken) {
            await notifyFailure(
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
