/**
 * Agent Service HTTP 运行时（25.2-05 / D-02 / D-03）。
 *
 * `node:http` 服务，默认 :3100（config.port）。提供：
 * - `POST /agent/novels/{novel_id}/runs`：SSE 流式运行。客户端断开（req close）→
 *   `session.abort()` + 后端 cancel（disconnect-cancel，D-19）；`agent_end` 且
 *   stopReason=stop → 触发后端 finalize（唯一 artifact 写入在 FastAPI finalizer）；
 *   abort/cancel → 零 artifact（cancel-no-write 跨服务边界）。
 * - `GET /healthz`：存活探针。
 *
 * 运行分发授权（T-25.2-05-02）：agent-service 绝不信客户端自报身份——run 只由
 * FastAPI `POST /api/agent/novels/{novel_id}/skill-runs` 202 授权（owner 检查 +
 * per-run 内部令牌铸造），该内部令牌随后作为工具门面的 authorization。
 *
 * 存储策略（DECISION go-fallback）：会话按 run 存于内存；全部持久状态在
 * skill_runs / 产物（FastAPI 侧）。本服务不接任何会话存储适配器。
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { config } from "./config.js";
import { createSseRunStream, piEventToFrame } from "./transport/sse.js";
import {
  loadSkill as defaultLoadSkill,
  validateRunInput as defaultValidateRunInput,
  validateRunOutput as defaultValidateRunOutput,
} from "./skills/loader.js";
import { createSession as defaultCreateSession } from "./agent/session-factory.js";
import type { LoadedSkill } from "./skills/loader.js";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { verifyLockfile } from "./governance/lockfile.js";
import { validatePermissionManifests } from "./governance/permission-manifest.js";
import {
  buildToolRegistryManifest,
  domainToolEntries,
  extensionToolEntries,
  mcpProxyEntry,
  skillToolEntries,
  verifySchemaHashes,
  type ToolRegistryEntry,
} from "./governance/tool-registry-manifest.js";
import { PolicyDenied, evaluate, loadSkillRules, type DomainActionRule } from "./policy/engine.js";
import { SessionApprovals } from "./policy/session-approvals.js";
import type { SseFrame } from "./transport/sse.js";
import {
  buildCitedAnswerEnvelope,
  type RunLineageContext,
  type ToolEvidence,
} from "./structured-output/cited-answer-builder.js";
import {
  buildAnalysisEnvelope,
  isAnalysisSkill,
} from "./structured-output/analysis-envelope-builder.js";
import { createPoller } from "./poller.js";

/** 依赖注入点（测试用 mock；生产用默认实现）。 */
export interface ServerDeps {
  fetchImpl?: typeof fetch;
  createSessionImpl?: typeof defaultCreateSession;
  loadSkillImpl?: typeof defaultLoadSkill;
  validateRunInputImpl?: typeof defaultValidateRunInput;
  validateRunOutputImpl?: typeof defaultValidateRunOutput;
}

/** 运行请求体形状（POST /agent/novels/{novel_id}/runs）。 */
export interface RunRequest {
  question: string;
  skill?: string;
  input?: Record<string, unknown>;
  branch?: string;
}

/** 启动治理链的可配置路径（测试注入 fixture 目录；生产默认 agent-service 工作目录）。 */
export interface GovernancePaths {
  packagesLockPath?: string;
  packageLockPath?: string;
  packageJsonPath?: string;
  /** 记录哈希表（如上次启动持久化的 manifest）→ schema drift 启动门。 */
  expectedSchemaHashes?: Record<string, string>;
}

/**
 * D-04..D-06 启动治理链：verifyLockfile → validatePermissionManifests →
 * buildToolRegistryManifest（碰撞门）→（可选）schema drift 门。任何失败抛错，
 * 调用方（startServer）先于 listen 以非零码退出——绝不带病接受连接。
 */
export function runGovernanceChain(paths: GovernancePaths = {}): ToolRegistryEntry[] {
  const lock = verifyLockfile(paths.packagesLockPath, paths.packageLockPath, paths.packageJsonPath);
  validatePermissionManifests(lock);
  const manifest = buildToolRegistryManifest([
    domainToolEntries(),
    skillToolEntries(lock),
    extensionToolEntries(lock),
    lock.mcp?.enabled ? [mcpProxyEntry()] : [],
  ]);
  if (paths.expectedSchemaHashes) {
    verifySchemaHashes(manifest, paths.expectedSchemaHashes);
  }
  return manifest;
}

/** 从请求流读取并解析 JSON body。 */
function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw.trim()) {
        reject(new Error("empty body"));
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error("invalid json"));
      }
    });
    req.on("error", reject);
  });
}

/** ask 轮询返回的决策 verdict。 */
export type ApprovalVerdict = "approved" | "approved_for_session" | "denied";

/**
 * 意图→skill 自动路由（AGENT-RUNTIME-CONTRACT：The Agent selects versioned Skills）。
 *
 * body.skill 缺省时调用 FastAPI `POST /api/agent/novels/{novel_id}/route-skill`
 * 按问题文本自动选 skill（服务端决策）。端点不可用/失败 → 保守回退
 * answer-reading-question（不破坏既有默认行为）。
 */
async function routeSkillByIntent(
  deps: ReturnType<typeof resolveDeps>,
  base: string,
  novelId: number,
  question: string,
  authHeader: string,
): Promise<string> {
  try {
    const res = await deps.fetchImpl(`${base}/api/agent/novels/${novelId}/route-skill`, {
      method: "POST",
      headers: { authorization: authHeader, "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) return "answer-reading-question";
    const body = (await res.json()) as { skills?: string[] };
    const skill = body.skills?.[0];
    return typeof skill === "string" && skill ? skill : "answer-reading-question";
  } catch {
    return "answer-reading-question";
  }
}

/** waitForApproval 短轮询选项。 */
export interface WaitForApprovalOptions {
  fetchImpl: typeof fetch;
  baseUrl: string;
  /** ApprovalRequest id（POST /approval-requests 的响应）。 */
  requestId: number;
  /** 端用户 JWT（转发；owner 隔离由 FastAPI 强制）。 */
  authHeader: string;
  /** run 取消信号：abort 即视为 denied（取消停止轮询）。 */
  signal?: AbortSignal;
  /** 轮询间隔（默认 2s）。 */
  intervalMs?: number;
}

/** abort 感知 sleep。 */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    if (signal?.aborted) {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * 短轮询 FastAPI ApprovalRequest（D-11 / open question 1 RESOLVED：SSE 推送 + 短轮询）。
 * - approved → "approved"；approved_for_session → "approved_for_session"；
 *   rejected/expired/cancelled → "denied"；run 取消（signal abort）→ "denied"。
 * - SSE 帧只通知、FastAPI 决定——本函数**不做**任何本地决策权威（T-25.3-04-02）。
 */
export async function waitForApproval(opts: WaitForApprovalOptions): Promise<ApprovalVerdict> {
  const intervalMs = opts.intervalMs ?? 2000;
  for (;;) {
    if (opts.signal?.aborted) return "denied";
    try {
      const res = await opts.fetchImpl(
        `${opts.baseUrl}/api/agent/approval-requests/${opts.requestId}`,
        { headers: { authorization: opts.authHeader } },
      );
      if (res.ok) {
        const body = (await res.json()) as { status?: string };
        if (body.status === "approved") return "approved";
        if (body.status === "approved_for_session") return "approved_for_session";
        if (body.status && body.status !== "pending") return "denied";
      }
    } catch {
      // 网络抖动：继续轮询；abort 由信号分支兜底。
    }
    try {
      await sleep(intervalMs, opts.signal);
    } catch {
      return "denied"; // AbortError：run 已取消
    }
  }
}

/** gateAction 选项：把单个域动作经策略引擎求值并走 Web Approval round trip。 */
export interface GateActionOptions {
  action: string;
  skillRules: DomainActionRule[];
  sessionApprovals: SessionApprovals;
  runId: string;
  novelId: number;
  authHeader: string;
  baseUrl: string;
  fetchImpl: typeof fetch;
  /** SSE 写入器（approval_request 帧只通知浏览器渲染）。 */
  stream: { send(frame: SseFrame): void };
  /** run 取消信号（agent-service 侧为 runAbort）。 */
  signal?: AbortSignal;
  /** 轮询间隔（默认 2s；测试注入小值加速）。 */
  intervalMs?: number;
  /** 规范化载荷摘要（不承载原始工具 I/O，T-25.3-04-06）。 */
  payloadSummary?: Record<string, unknown>;
}

/**
 * 前置门控（D-10/D-11）：evaluate 后——
 * - allow：直接放行；
 * - deny：抛 PolicyDenied（无审批路径，进 run 稳定错误路径）；
 * - ask：POST /api/agent/approval-requests → 发 approval_request SSE 帧 →
 *   waitForApproval 短轮询；approved 放行、approved_for_session 写入本 run 会话批准、
 *   其余 verdict（rejected/expired/cancelled/aborted）抛 PolicyDenied。
 */
export async function gateAction(opts: GateActionOptions): Promise<void> {
  const decision = evaluate(opts.action, {
    skillRules: opts.skillRules,
    sessionApprovals: opts.sessionApprovals.asReadonly(),
  });
  if (decision === "allow") return;
  if (decision === "deny") {
    throw new PolicyDenied(opts.action, "全局 deny（无审批路径，D-10）");
  }

  const res = await opts.fetchImpl(`${opts.baseUrl}/api/agent/approval-requests`, {
    method: "POST",
    headers: { authorization: opts.authHeader, "content-type": "application/json" },
    body: JSON.stringify({
      run_id: Number(opts.runId),
      novel_id: opts.novelId,
      action: opts.action,
      payload_summary: opts.payloadSummary ?? { action: opts.action },
    }),
  });
  if (!res.ok) {
    throw new PolicyDenied(opts.action, `审批请求创建失败 (HTTP ${res.status})`);
  }
  const request = (await res.json()) as { id: number; action?: string };
  // 只通知：浏览器渲染对话框；决策仍在 FastAPI（SSE 帧不携带任何决策权威）。
  opts.stream.send({ type: "approval_request", request });

  const verdict = await waitForApproval({
    fetchImpl: opts.fetchImpl,
    baseUrl: opts.baseUrl,
    requestId: request.id,
    authHeader: opts.authHeader,
    signal: opts.signal,
    intervalMs: opts.intervalMs,
  });
  if (verdict === "approved") return;
  if (verdict === "approved_for_session") {
    // D-11 会话级批准：仅本 run 内存有效，绝不沉淀为常驻权限（A5 / ASVS V3）。
    opts.sessionApprovals.add(opts.action);
    return;
  }
  throw new PolicyDenied(opts.action, `审批被拒绝/过期/取消（verdict=${verdict}）`);
}

/** 取最后一条 assistant 消息（含 stopReason/usage）。 */
function lastAssistantMessage(messages: readonly unknown[]): {
  stopReason?: string;
  provider?: string;
  model?: string;
  usage?: unknown;
  text?: string;
} | undefined {
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

/**
 * 从会话消息提取成功只读工具调用的证据（answer-reading-question 物化
 * evidence_refs）。只收 toolResult 且 isError=false 的调用；文本内容截取
 * 前 2000 字符作为证据正文。
 */
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

/** 构造依赖（默认 + 注入覆盖）。 */
function resolveDeps(deps: ServerDeps = {}) {
  return {
    fetchImpl: deps.fetchImpl ?? (globalThis.fetch as typeof fetch),
    createSessionImpl: deps.createSessionImpl ?? defaultCreateSession,
    loadSkillImpl: deps.loadSkillImpl ?? defaultLoadSkill,
    validateRunInputImpl: deps.validateRunInputImpl ?? defaultValidateRunInput,
    validateRunOutputImpl: deps.validateRunOutputImpl ?? defaultValidateRunOutput,
  };
}

/**
 * 处理一次 run 请求。失败以状态码 + JSON error 结束（SSE 头未写时）；
 * 成功后以 text/event-stream 流式返回。
 */
async function handleRun(
  req: IncomingMessage,
  res: ServerResponse,
  novelId: number,
  deps: ReturnType<typeof resolveDeps>,
  manifest: ToolRegistryEntry[],
): Promise<void> {
  const authHeader = req.headers.authorization;
  if (!authHeader) {
    res.writeHead(401, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "unauthorized", message: "缺少 Authorization 头" } }));
    return;
  }

  let rawBody: unknown;
  try {
    rawBody = await readJsonBody(req);
  } catch {
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "invalid_input", message: "请求体必须是合法 JSON" } }));
    return;
  }
  const body = (rawBody ?? {}) as Partial<RunRequest>;
  const question = typeof body.question === "string" ? body.question.trim() : "";
  const base = config.fastApiBaseUrl;

  // 技能选择（AGENT-RUNTIME-CONTRACT）：客户端显式 body.skill 是高级覆盖；
  // 缺省 → 服务端按问题意图自动路由（Agent 选 skill，用户不选）。
  let skillName: string;
  if (typeof body.skill === "string" && body.skill) {
    skillName = body.skill;
  } else {
    skillName = await routeSkillByIntent(deps, base, novelId, question, authHeader);
  }
  let skill: LoadedSkill;
  try {
    skill = deps.loadSkillImpl(skillName);
  } catch (err) {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "not_found", message: `技能不存在或校验失败: ${(err as Error).message}` } }));
    return;
  }

  // SSE 端点是问答驱动（默认 answer-reading-question）：只有它把 question
  // 注入 input 并强制非空。分析/生图类 skill（illustrate-scene 等）的
  // input.schema 不含 question 且 additionalProperties:false——注入会 422，
  // 它们用 body.input 原样 + question 作模型 prompt（可选）。
  const isQaSkill = skillName === "answer-reading-question";
  if (isQaSkill && !question) {
    res.writeHead(422, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "invalid_input", message: "input.question 必须为非空字符串" } }));
    return;
  }

  const input: Record<string, unknown> = {
    novel_id: novelId,
    ...(isQaSkill ? { question } : {}),
    ...(typeof body.input === "object" && body.input !== null ? body.input : {}),
  };
  try {
    deps.validateRunInputImpl(skill, input);
  } catch (err) {
    res.writeHead(422, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "invalid_input", message: (err as Error).message } }));
    return;
  }

  const jsonHeaders: Record<string, string> = {
    authorization: authHeader,
    "content-type": "application/json",
  };

  // 1) 解析 active 技能版本（唯一 run 授权入口的输入）。
  let activeVersionId: number;
  try {
    const versionRes = await deps.fetchImpl(`${base}/api/agent/skills/${skillName}/versions`, {
      method: "GET",
      headers: { authorization: authHeader },
    });
    if (!versionRes.ok) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { code: "upstream_error", message: "技能版本查询失败" } }));
      return;
    }
    const { items } = (await versionRes.json()) as { items?: Array<{ id: number; status: string }> };
    const active = (items ?? []).find((v) => v.status === "active");
    if (!active) {
      res.writeHead(422, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { code: "invalid_input", message: "技能版本不可用（无 active 版本）" } }));
      return;
    }
    activeVersionId = active.id;
  } catch {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "upstream_error", message: "技能版本查询失败" } }));
    return;
  }

  // 2) FastAPI run-create：owner 校验 + commit-before-dispatch + per-run 内部令牌。
  let runId: string;
  let internalToken: string;
  let runLineage: RunLineageContext | undefined;
  try {
    const runRes = await deps.fetchImpl(`${base}/api/agent/novels/${novelId}/skill-runs`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        skill_version_id: activeVersionId,
        input,
        branch: typeof body.branch === "string" ? body.branch : null,
      }),
    });
    if (runRes.status !== 202) {
      const errorBody = (await runRes.json().catch(() => null)) as { error?: { code?: string } } | null;
      res.writeHead(runRes.status, { "content-type": "application/json" });
      res.end(
        JSON.stringify({
          error: {
            code: errorBody?.error?.code ?? "upstream_error",
            message: `run 创建失败 (HTTP ${runRes.status})`,
          },
        }),
      );
      return;
    }
    const accepted = (await runRes.json()) as {
      run: {
        id: number | string;
        owner_id: number;
        novel_id: number;
        skill_version_id: number;
        input_hash: string;
      };
      internal_token: string;
    };
    runId = String(accepted.run.id);
    internalToken = accepted.internal_token;
    runLineage = {
      runId: String(accepted.run.id),
      ownerId: accepted.run.owner_id,
      novelId: accepted.run.novel_id,
      skillVersionId: accepted.run.skill_version_id,
      inputHash: accepted.run.input_hash,
    };
  } catch {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "upstream_error", message: "run 创建失败" } }));
    return;
  }

  // 3) 用 per-run 内部令牌创建会话（工具门面授权；不信任客户端身份）。
  //    manifest（D-06 单一 allowlist 源）随会话传入：启用条目才是可调用工具集。
  let session: AgentSession;
  try {
    session = await deps.createSessionImpl({
      auth: `Bearer ${internalToken}`,
      novelId,
      skill,
      manifest,
    });
  } catch (err) {
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "upstream_error", message: `会话创建失败: ${(err as Error).message}` } }));
    return;
  }

  // 4) SSE 流。
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  // 立即发送响应头：writeHead 后不 flush 会延迟到首个 body 或连接关闭，
  // 客户端 fetch 将一直挂起（SSE 必须尽快握手）。
  res.flushHeaders();
  const stream = createSseRunStream(res);

  // run 级取消信号：客户端断开 / 取消 → runAbort.abort() → 审批轮询停止并解析 denied。
  const runAbort = new AbortController();

  let done = false;
  let cancelPosted = false;
  const postCancel = async (): Promise<void> => {
    if (cancelPosted) return;
    cancelPosted = true;
    try {
      await deps.fetchImpl(`${base}/api/agent/novels/${novelId}/skill-runs/${runId}/cancel`, {
        method: "POST",
        headers: { authorization: authHeader },
      });
    } catch {
      // 尽力而为：cancel 通知失败不阻断已结束的流。
    }
  };

  const unsub = session.subscribe((event) => {
    if (done) return;
    const frame = piEventToFrame(event);
    if (frame) stream.send(frame);
  });

  // 客户端断开 → 取消 in-flight 会话 + 后端 cancel（disconnect-cancel，D-19）。
  // req "close" 是连接级事件（plan 验收），res "close" 在 Node http 中最先触发
  // （探针确认顺序 res:close → socket:close → req:close）——两个都挂，标志位去重。
  let closed = false;
  const onClose = (): void => {
    if (closed) return;
    closed = true;
    done = true;
    unsub();
    runAbort.abort();
    void session.abort();
    void postCancel();
  };
  req.on("close", onClose);
  res.on("close", onClose);

  try {
    // 前置门控（25.3-04/06 / D-10/D-11）：skill 声明需审批的域动作在 prompt 前
    // 经策略引擎求值——deny 抛 PolicyDenied、ask 走 Web Approval round trip。
    // 会话级批准按 run 存于内存（SessionApprovals，无持久化路径）。
    const sessionApprovals = new SessionApprovals();
    const skillRules = loadSkillRules(skill.approvalRequiredFor);
    for (const action of skill.approvalRequiredFor) {
      await gateAction({
        action,
        skillRules,
        sessionApprovals,
        runId,
        novelId,
        authHeader,
        baseUrl: base,
        fetchImpl: deps.fetchImpl,
        stream,
        signal: runAbort.signal,
      });
    }

    await session.prompt(
      question || `使用 ${skillName} 技能执行任务，按技能描述完成分析/生成。`,
    );
    const last = lastAssistantMessage(session.messages as unknown[]);
    const stopReason = last?.stopReason ?? "error";

    if (stopReason === "stop") {
      // 输出先行本地校验（finalize 侧还有服务端校验）。
      try {
        deps.validateRunOutputImpl(skill, last?.text);
      } catch {
        // 输出不合规仍走 finalize（FastAPI 有权威校验与错误码）。
      }
      try {
        // 从会话工具调用提取成功证据（get_chapter/get_novel 等只读工具结果），
        // 构造带 evidence_refs + normalization trail 的完整信封。
        const evidences = extractToolEvidences(session.messages as unknown[]);
        let envelopePayload: Record<string, unknown> | undefined;
        let frozenManifest: Record<string, unknown> | undefined;
        if (runLineage && skill.name === "answer-reading-question") {
          try {
            const built = buildCitedAnswerEnvelope(
              last?.text ?? "",
              runLineage,
              skill,
              evidences,
            );
            envelopePayload = built.envelope;
            frozenManifest = built.frozenManifest;
          } catch {
            // 证据不足（如 stub/测试会话无工具调用）：回退简化信封，由后端
            // integrity 门最终裁决（fail closed）。
            envelopePayload = last?.text
              ? { type: "cited_answer", answer: { answer_blocks: [{ text: last.text }] } }
              : {};
          }
        } else if (runLineage && isAnalysisSkill(skill.name)) {
          // 分析/生图 skill：模型输出是结构化 JSON，按 skill 构造对应
          // envelope.type（scene_candidate / world_model_candidate /
          // visual_bible）。解析失败或无 leaf 证据 → 抛错（诚实失败，
          // 绝不伪造 cited_answer 信封导致假 artifact）。
          if (!last?.text) {
            throw new Error(`SSE run: skill ${skill.name} returned no model output`);
          }
          const built = buildAnalysisEnvelope(
            last.text,
            runLineage,
            skill,
            null,
          );
          envelopePayload = built.envelope;
          frozenManifest = built.frozenManifest;
        } else {
          // 未接线的 skill：诚实失败（不伪造 cited_answer envelope）。
          throw new Error(`SSE run: no envelope builder for skill ${skill.name}`);
        }
        const finalizeRes = await deps.fetchImpl(
          `${base}/api/agent/novels/${novelId}/skill-runs/${runId}/finalize`,
          {
            method: "POST",
            headers: jsonHeaders,
            body: JSON.stringify({
              stop_reason: "stop",
              envelope: envelopePayload,
              model_lineage: last?.provider && last?.model ? { provider: last.provider, model: last.model } : {},
              source_versions: {},
              usage: last?.usage ?? {},
              ...(frozenManifest ? { frozen_manifest: frozenManifest } : {}),
            }),
          },
        );
        if (finalizeRes.ok) {
          const finalizeBody = (await finalizeRes.json().catch(() => null)) as {
            artifact?: unknown;
          } | null;
          if (finalizeBody?.artifact) {
            stream.send({ type: "artifact", artifact: finalizeBody.artifact });
          }
        }
      } catch {
        // finalize 网络失败：run 状态以 FastAPI 侧为准。
      }
      stream.send({ type: "run_end", runId, status: "completed" });
    } else if (stopReason === "aborted") {
      await postCancel();
      stream.send({ type: "run_end", runId, status: "cancelled" });
    } else {
      // 上游/运行异常（stopReason 非 stop/aborted，如 "error"/"max_tokens"/"other"）：
      // 必须调后端 finalize 落库（failed + 0 artifact），否则 run 会永久卡 queued。
      // 不用 cancel——queued→cancelled 语义错误；finalize 对非 stop reason 写 failed。
      // done=true（客户端断开）时跳过：onClose 已 postCancel（cancel-no-write），
      // 再 finalize 会造成 cancel/finalize 双重状态迁移。
      if (!done) {
        try {
          await deps.fetchImpl(
            `${base}/api/agent/novels/${novelId}/skill-runs/${runId}/finalize`,
            {
              method: "POST",
              headers: jsonHeaders,
              body: JSON.stringify({
                stop_reason: "error",
                envelope: {},
                model_lineage: last?.provider && last?.model ? { provider: last.provider, model: last.model } : {},
                source_versions: {},
                usage: last?.usage ?? {},
              }),
            },
          );
        } catch {
          // finalize 网络失败：run 状态以后端为准（尽力而为，不阻塞终帧）。
        }
      }
      stream.send({ type: "run_end", runId, status: "failed", error_code: "upstream_error" });
    }
  } catch (err) {
    // prompt/门控异常（含外部 abort）：cancel-no-write 语义——通知 cancel，发 failed 终帧。
    // PolicyDenied（deny 或审批被拒/过期/取消）走稳定的 policy_denied 错误码。
    await postCancel();
    stream.send({
      type: "run_end",
      runId,
      status: "failed",
      error_code: err instanceof PolicyDenied ? "policy_denied" : "upstream_error",
    });
  } finally {
    done = true;
    req.off("close", onClose);
    res.off("close", onClose);
    stream.close();
  }
}

/** 创建 HTTP 服务（不 listen；测试自行 listen）。manifest 由启动治理链产出，
 *  缺省回退到域工具清单（测试/退化路径仍保持 D-06 语义：allowlist 源自 manifest）。 */
export function createApp(deps: ServerDeps = {}, manifest?: ToolRegistryEntry[]): ReturnType<typeof createServer> {
  const resolved = resolveDeps(deps);
  const activeManifest = manifest ?? domainToolEntries();
  return createServer((req, res) => {
    void (async () => {
      const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);

      if (req.method === "GET" && url.pathname === "/healthz") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ status: "ok" }));
        return;
      }

      const match = url.pathname.match(/^\/agent\/novels\/(\d+)\/runs$/);
      if (req.method === "POST" && match) {
        await handleRun(req, res, Number(match[1]), resolved, activeManifest);
        return;
      }

      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { code: "not_found", message: "路径不存在" } }));
    })().catch(() => {
      if (!res.headersSent) {
        res.writeHead(500, { "content-type": "application/json" });
      }
      res.end();
    });
  });
}

/** 启动服务（部署入口）：先跑启动治理链（D-04..D-06，fail-closed），再 listen。
 *  治理失败 → 记录错误并以非零码退出——进程绝不带病接受连接（T-25.3-02-01/02/03）。 */
export function startServer(deps: ServerDeps = {}): Promise<ReturnType<typeof createServer>> {
  let manifest: ToolRegistryEntry[];
  try {
    manifest = runGovernanceChain();
  } catch (err) {
    console.error(`[agent-service] 启动治理失败（fail-closed，拒绝监听）: ${(err as Error).message}`);
    process.exit(1);
    throw err; // 不可达（process.exit 恒不返回）；满足严格类型下 manifest 的 definite assignment
  }
  const server = createApp(deps, manifest);
  // 问答按需分析（chat_backfill）：启动 queued-run poller（无 SSE 客户端场景）。
  if (config.pollEnabled) {
    const stopPoller = createPoller(
      { fetchImpl: resolveDeps(deps).fetchImpl },
      manifest,
    ).start();
    server.on("close", stopPoller);
  }
  return new Promise((resolve) => {
    server.listen(config.port, () => resolve(server));
  });
}
