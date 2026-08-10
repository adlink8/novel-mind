/**
 * fetch-stream SSE 助手（25.2-04 Agent Workspace）+ 44-03 重连/终态语义。
 *
 * 为什么不用 EventSource：原生 EventSource 无法携带 Authorization 头（Pitfall 4），
 * 因此用 fetch + `res.body.getReader()` 手写解析（零新依赖，~50 行）：
 * - POST + `Authorization: Bearer <token>`（桌面模式 token 经 bridge 会话令牌，
 *   `X-Local-Auth-Token` 承载局部会话凭据；浏览器模式沿用 api.ts token 来源）。
 * - 按 "\n\n" 分帧；跨 chunk 边界的帧缓存拼接，直到完整才派发。
 * - 每帧取 `data:` 行并 JSON.parse → onEvent；畸形帧 → onError，流继续。
 * - 非 2xx → 抛出带 status 的 Error，绝不派发任何伪造帧。
 * - abort → `DOMException("Aborted", "AbortError")`（与 pollReaderChatJob 同约定）。
 *
 * 44-03 终端语义（D-44-05 / T-44-03-01/02）：
 * - `runAgentStream` 在 `streamAgentRun` 之上实现单次重连：仅在**已收到帧**但连接
 *   中途断开时重试一次（服务端在超时前已开始流式输出），用同一 POST body 重新建流；
 *   建立连接前/非 2xx/流终止后的断开 → 不重连，保持权威 `failed`/`cancelled`。
 * - 终端帧（run_end status completed|cancelled|failed）到达后立即关闭流：之后的任何
 *   连接异常（超时/断开/重复事件 ID）都不得把终态改成成功（绝不合成成功）。
 * - 每条事件帧的 `event_id` 由调用方从 run 会话导入；驱动层做去重，重放帧不触发
 *   二次物化（T-44-03-01 idempotent materialization）。
 * - 会话旋转（运行时重启/令牌轮换）：`resolveSession` 拿到新 sessionId 后旧流立即
 *   失效（abort + reject rotation），不再向已旋转的服务重试（T-44-03-02）。
 */

import { getAccessToken } from "./api/client";
import { endpointResolver } from "./runtime/endpoint-resolver";
import { desktopCapabilities } from "./desktop/capabilities";

/** 智能体 SSE 帧形状（25.2-05 + 25.3-06 契约）：type 之外的字段随帧类型变化。 */
export interface AgentRunFrame {
  type:
    | "delta"
    | "tool_start"
    | "tool_end"
    | "turn_end"
    | "artifact"
    | "run_end"
    | "approval_request";
  [key: string]: unknown;
}

export interface StreamAgentRunOptions {
  signal?: AbortSignal;
  /** 每解析出一帧（JSON.parse 成功）即回调。 */
  onEvent: (frame: AgentRunFrame) => void;
  /** 畸形帧回调；流不中断，继续解析后续帧。 */
  onError?: (error: unknown) => void;
}

/** 从一段 SSE 文本中提取 `data:` 行的原始载荷。 */
function extractDataPayload(frameText: string): string {
  const dataLines = frameText
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"));
  if (dataLines.length === 0) return "";
  return dataLines
    .map((line) => line.slice("data:".length).trim())
    .join("\n");
}

function dispatchFrame(
  frameText: string,
  onEvent: (frame: AgentRunFrame) => void,
  onError?: (error: unknown) => void
): void {
  const raw = extractDataPayload(frameText);
  if (!raw) return;
  try {
    onEvent(JSON.parse(raw) as AgentRunFrame);
  } catch (err) {
    // 畸形帧只通知调用方，不中断流。
    onError?.(err);
  }
}

/** 权威 run 终态（D-44-05）：终帧到达后流即关闭，状态不可再被改写。 */
export type RunTerminalStatus = "completed" | "cancelled" | "failed";

export function isRunTerminal(frame: AgentRunFrame): frame is AgentRunFrame & {
  type: "run_end";
  status: RunTerminalStatus;
} {
  return (
    frame.type === "run_end" &&
    (frame.status === "completed" || frame.status === "cancelled" || frame.status === "failed")
  );
}

export interface SseRunResolution {
  /** Absolute loopback origin of the agent service, or "" (browser relative). */
  baseUrl: string;
  /** The runtime session this stream is bound to (desktop), or null (browser). */
  sessionId: string | null;
  /**
   * Short-lived local-session token for the agent service (44-03), or null
   * when unavailable. Only present when a desktop runtime session exists.
   */
  localAuthToken: string | null;
  /** End-user JWT (unchanged source of the pre-existing auth flow). */
  endUserToken: string | null;
}

/**
 * Resolve the agent SSE transport inputs (endpoint + auth) for this request.
 * Failures map to a typed resolution — never a guessed URL, never a raw secret
 * (T-44-01-01/02). Browser mode keeps the existing relative-route semantics.
 */
export async function resolveSseRunResolution(): Promise<SseRunResolution> {
  const endUserToken = getAccessToken();
  const resolution = await endpointResolver.resolve();
  if (resolution.kind !== "desktop") {
    return { baseUrl: "", sessionId: null, localAuthToken: null, endUserToken };
  }

  let localAuthToken: string | null = null;
  if (desktopCapabilities.isDesktop) {
    const capability = await desktopCapabilities.getLocalAuthToken("agent");
    if (capability.supported) localAuthToken = capability.value;
  }
  return {
    baseUrl: resolution.endpoints.agentBaseUrl,
    sessionId: resolution.sessionId,
    localAuthToken,
    endUserToken,
  };
}

/**
 * POST + 流式读取 agent run SSE（单次连接）。
 *
 * 约定：
 * - 非 2xx → `throw`（Error 带 `status` 字段）；响应无 body → throw。
 * - 读流过程中 abort → 以 `DOMException("Aborted", "AbortError")` reject。
 * - 只派发服务端发来的帧，绝不客户端伪造 assistant 内容。
 * - 桌面模式下 agent 服务 base 来自 endpoint-resolver（D-44-01）且以
 *   `X-Local-Auth-Token` 携带 44-03 短命会话令牌；浏览器模式保持相对路径与
 *   既有 Bearer 语义（next rewrite）。
 */
export async function streamAgentRun(
  url: string,
  body: unknown,
  options: StreamAgentRunOptions,
  resolution?: SseRunResolution
): Promise<void> {
  const resolved =
    resolution ?? (await resolveSseRunResolution());
  const resolvedUrl =
    resolved.baseUrl !== "" && url.startsWith("/")
      ? `${resolved.baseUrl}${url}`
      : url;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (resolved.localAuthToken !== null && resolved.localAuthToken !== "") {
    // 44-03：本地会话令牌与端用户 JWT 分离承载。agent-service 先验会话令牌
    // （fail closed），再把端用户 JWT 转发给 FastAPI 做 owner 校验。
    headers["X-Local-Auth-Token"] = resolved.localAuthToken;
    headers["Authorization"] = `Bearer ${resolved.endUserToken ?? ""}`;
  } else {
    headers["Authorization"] = `Bearer ${resolved.endUserToken ?? ""}`;
  }

  const res = await fetch(resolvedUrl, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: options.signal,
  });
  if (!res.ok) {
    const err = new Error(`agent run failed with HTTP ${res.status}`) as Error & {
      status?: number;
    };
    err.status = res.status;
    throw err;
  }
  if (!res.body) {
    throw new Error("agent run response has no readable body");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // stream:true：多字节字符跨 chunk 时由 decoder 内部保持。
      buffer += decoder.decode(value, { stream: true });
      // 分帧：完整帧以 "\n\n" 结束；不完整的尾部帧留在 buffer 等下一 chunk。
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        dispatchFrame(buffer.slice(0, sep), options.onEvent, options.onError);
        buffer = buffer.slice(sep + 2);
        sep = buffer.indexOf("\n\n");
      }
    }
    // 流结束后冲刷残余：最后一帧可能没有尾随 "\n\n"。
    if (buffer.trim()) {
      dispatchFrame(buffer, options.onEvent, options.onError);
    }
  } catch (err) {
    if (options.signal?.aborted || (err as Error)?.name === "AbortError") {
      throw new DOMException("Aborted", "AbortError");
    }
    throw err;
  }
}

export interface RunAgentStreamParams {
  url: string;
  body: unknown;
  signal?: AbortSignal;
  /** 当前 run 的 event id（run 起点），用于事件去重（T-44-03-01）。 */
  eventId?: string | number | null;
  /** 事件帧回调（按服务端事件序）。 */
  onEvent: (frame: AgentRunFrame) => void;
  /** 畸形帧回调；流不中断。 */
  onError?: (error: unknown) => void;
}

/** 从事件帧提取稳定的去重键；无键事件用 null 表示不参与去重。 */
function eventDedupeKey(frame: AgentRunFrame): string | null {
  const raw = frame.event_id ?? frame.eventId;
  if (typeof raw === "string" && raw !== "") return raw;
  if (typeof raw === "number") return String(raw);
  return null;
}

/**
 * 44-03 驱动层：以权威终态 + 单次重连 + 去重语义运行 agent 流。
 *
 * 行为（D-44-05 / T-44-03-01/02）：
 * - 每次连接前解析会话（端点 + 本地会话令牌）。解析失败 → reject 带 `code`
 *   （"bootstrap-unavailable"），绝不猜测 URL。
 * - 建立连接成功后若已收到事件帧再断开 → 单次重连（同一 POST body）；连接前
 *   失败 / 非 2xx / 终帧之后的断开 → 不重连。
 * - run_end 终帧（completed|cancelled|failed）到达后关闭流并 resolve；此后任何
 *   网络异常都不会把终态改写为成功（绝不合成成功）。
 * - 事件去重：`event_id` 已派发过的帧被丢弃，不触发二次物化。
 * - 会话旋转：重连前若 sessionId 变化（运行时重启/令牌轮换），旧会话立即失效，
 *   reject `code:"session-rotated"`（调用方触发干净重引导，T-44-03-02）。
 * - 外部 abort → `DOMException("Aborted", "AbortError")`（取消保持 cancelled）。
 */
export async function runAgentStream(
  params: RunAgentStreamParams,
): Promise<RunTerminalStatus> {
  const seen = new Set<string>();
  let emittedAnyEvent = false;
  let terminal: RunTerminalStatus | null = null;
  let consumedSessionId: string | null = null;
  const active = params.signal;

  const dispatch = (frame: AgentRunFrame): void => {
    if (terminal !== null) return; // 终态后忽略一切后续帧
    const key = eventDedupeKey(frame);
    if (key !== null) {
      if (seen.has(key)) return; // 重放：不触发二次物化（T-44-03-01）
      seen.add(key);
    }
    emittedAnyEvent = true;
    params.onEvent(frame);
    if (isRunTerminal(frame)) {
      terminal = frame.status;
    }
  };

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const resolution = await resolveSseRunResolution();
    if (
      consumedSessionId !== null &&
      resolution.sessionId !== null &&
      resolution.sessionId !== consumedSessionId
    ) {
      const err = new Error("agent runtime session rotated mid-stream") as Error & {
        code?: string;
      };
      err.code = "session-rotated";
      throw err;
    }
    // Remember the session this stream binds to as soon as it is resolved —
    // even if the attempt fails, a reconnect must never resume against a
    // rotated session (T-44-03-02).
    if (consumedSessionId === null) consumedSessionId = resolution.sessionId;

    try {
      await streamAgentRun(params.url, params.body, {
        signal: active,
        onEvent: dispatch,
        onError: params.onError,
      }, resolution);
    } catch (err) {
      if (active?.aborted || (err as Error)?.name === "AbortError") {
        throw new DOMException("Aborted", "AbortError");
      }
      const coded = err as Error & { code?: string; status?: number };
      if (coded.code === "session-rotated") throw err;
      if (typeof coded.status === "number") {
        // 非 2xx（401/404/502…）：服务端已明确拒绝，重连只会得到同一结果。
        throw err;
      }
      // 网络/传输层失败：仅在已收到事件帧后的中途断开时单次重连（服务端已开始
      // 流式输出）；连接前失败/终态后的断开保持权威失败（绝不合成成功）。
      if (emittedAnyEvent && attempt === 0) {
        continue;
      }
      throw err;
    }

    // 连接建立且读流正常结束：
    // - 收到终帧 → 完成（终态权威，D-44-05）。
    // - 未收到终帧但流已结束（超时/服务端静默断开）→ 不重连、不合成成功；
    //   以 failed 上报（T-44-03-02 绝不把超时翻译成成功）。
    return terminal ?? "failed";
  }
  // 理论上不可达（最多 2 次尝试）；保守返回 failed，绝不返回成功。
  return "failed";
}
