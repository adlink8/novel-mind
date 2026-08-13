/**
 * FastAPI 工具门面客户端（25.2-05 / D-07）。
 *
 * 转发工具调用到 `POST /api/agent-tools/{tool_name}`（25.2-02 facade），携带调用方
 * 的 `authorization` 原样透传（端用户 JWT 或 per-run 内部令牌）。强制 per-tool 30s
 * 超时、64 KiB 字节上限，并把 facade 的错误信封 `{error:{code}}` 归一化为冻结的
 * `AGENT_TOOL_ERRORS` 错误码（唯一镜像，与 25.2-02 冻结表精确一致）。
 *
 * 安全（V6 / T-25.2-05-01）：令牌（用户 JWT / 内部令牌 / gateway 令牌）绝不落日志、
 * 绝不出现在错误消息或 SSE 帧中。
 */

import { config } from "../config.js";

/** 冻结的 agent-tool 错误码镜像（与 backend/app/services/agent_tools/errors.py 精确一致）。 */
export const AGENT_TOOL_ERRORS = {
  FORBIDDEN: "forbidden",
  NOT_FOUND: "not_found",
  BEYOND_CUTOFF: "beyond_cutoff",
  BUDGET_EXCEEDED: "budget_exceeded",
  TIMEOUT: "timeout",
  OUTPUT_TOO_LARGE: "output_too_large",
  INVALID_INPUT: "invalid_input",
  UPSTREAM_ERROR: "upstream_error",
} as const;

/** 全部冻结错误码名称（单一事实源，供测试断言用）。 */
export const AGENT_TOOL_ERROR_CODES = Object.values(AGENT_TOOL_ERRORS);

/** 已知错误码集合：未知 code 一律归一为 upstream_error。 */
const KNOWN_CODES = new Set<string>(AGENT_TOOL_ERROR_CODES);

/** 单次工具响应体字节上限（64 KiB）。 */
export const TOOL_OUTPUT_BYTE_LIMIT = 64 * 1024;

/** per-tool 硬超时（30s）。 */
export const TOOL_TIMEOUT_MS = 30_000;

/** 工具门面错误：携带冻结错误码。 */
export class AgentToolError extends Error {
  readonly code: string;
  readonly httpStatus: number | undefined;

  constructor(code: string, message?: string, httpStatus?: number) {
    super(message ?? code);
    this.name = "AgentToolError";
    this.code = code;
    this.httpStatus = httpStatus;
  }
}

/** FastAPI facade 的错误信封形状。 */
interface FacadeErrorEnvelope {
  error?: { code?: string; message?: string };
  detail?: unknown;
}

/**
 * 解析非 2xx 响应的错误信封，把 `{error:{code}}` 归一化为冻结错误码。
 * 信封缺失或 code 未知 → `upstream_error`（绝不发明 agent-service 自有错误码）。
 */
function mapFacadeError(httpStatus: number, bodyText: string): AgentToolError {
  let code: string | undefined;
  let message: string | undefined;
  try {
    const parsed = JSON.parse(bodyText) as FacadeErrorEnvelope;
    if (parsed?.error?.code) {
      code = String(parsed.error.code);
      message = parsed.error.message;
    }
  } catch {
    // 非 JSON 响应体（如网关 HTML 错误页）→ upstream_error。
  }
  if (code && KNOWN_CODES.has(code)) {
    return new AgentToolError(code, message ?? code, httpStatus);
  }
  return new AgentToolError(AGENT_TOOL_ERRORS.UPSTREAM_ERROR, `upstream error (HTTP ${httpStatus})`, httpStatus);
}

/**
 * 调用一次 FastAPI 工具门面。
 *
 * @param name   工具名（25.2-02 冻结的 7 个之一）。
 * @param params 工具参数（TypeBox 已校验）。
 * @param signal 运行级 AbortSignal：abort 时取消 in-flight fetch（cancel-no-write 跨服务边界）。
 * @param auth   `authorization` 头原样透传（端用户 JWT 或 per-run 内部令牌）。绝不落日志。
 * @returns Pi 工具结果形状 `{ content, details }`。
 */
export async function fastapiToolCall(
  name: string,
  params: unknown,
  signal: AbortSignal | undefined,
  auth: string,
  runNovelId: number,
): Promise<{ content: [{ type: "text"; text: string }]; details: Record<string, never> }> {
  const runSignal = signal ?? new AbortController().signal;
  // per-tool 硬超时与运行取消的并集：任一触发即中断 fetch。
  const ctrl = AbortSignal.any([runSignal, AbortSignal.timeout(TOOL_TIMEOUT_MS)]);
  // novel_id 经查询参数注入（后端 require_owned_novel），且**始终使用 run 绑定的
  // novel_id**（不信任模型在工具参数里填的 novel_id——模型可能猜测/填错，auth 校验
  // 按 run.novel_id 强绑定）。body 只留工具自身参数。
  const { novel_id: _ignored, ...toolParams } = (params ?? {}) as Record<string, unknown>;
  const query = `?novel_id=${encodeURIComponent(String(runNovelId))}`;

  let res: Response;
  try {
    res = await fetch(`${config.fastApiBaseUrl}/api/agent-tools/${name}${query}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        // 原样透传：令牌只在请求头中流动，绝不写日志 / 错误信息。
        authorization: auth,
      },
      body: JSON.stringify(toolParams),
      signal: ctrl,
    });
  } catch (err) {
    // 运行级取消：向上传播（Pi 将其视为 run abort，最终 stopReason=aborted）。
    if (runSignal.aborted) {
      throw err;
    }
    const reason = (err as Error)?.name ?? "";
    if (reason === "TimeoutError" || reason === "AbortError") {
      throw new AgentToolError(AGENT_TOOL_ERRORS.TIMEOUT, `tool ${name} timed out after ${TOOL_TIMEOUT_MS}ms`);
    }
    throw new AgentToolError(AGENT_TOOL_ERRORS.UPSTREAM_ERROR, `tool ${name} network error: ${(err as Error)?.message ?? "unknown"}`);
  }

  // 先读全文再检查字节上限：超限即拒绝（无需解析）。
  const bodyText = await res.text();
  if (bodyText.length > TOOL_OUTPUT_BYTE_LIMIT) {
    throw new AgentToolError(AGENT_TOOL_ERRORS.OUTPUT_TOO_LARGE, `tool ${name} response exceeds ${TOOL_OUTPUT_BYTE_LIMIT} bytes`);
  }

  if (!res.ok) {
    throw mapFacadeError(res.status, bodyText);
  }

  return { content: [{ type: "text", text: bodyText }], details: {} };
}

/** Run-frozen restricted connector proxy; URL/version authority stays in FastAPI. */
export async function fastapiConnectorToolCall(
  toolName: string,
  params: unknown,
  signal: AbortSignal | undefined,
  auth: string,
  runNovelId: number,
): Promise<{ content: [{ type: "text"; text: string }]; details: Record<string, never> }> {
  const runSignal = signal ?? new AbortController().signal;
  const ctrl = AbortSignal.any([runSignal, AbortSignal.timeout(TOOL_TIMEOUT_MS)]);
  const connectorName = toolName.startsWith("connector:")
    ? toolName.slice("connector:".length)
    : "";
  if (!/^[a-z0-9]+(?:[-_][a-z0-9]+)*$/.test(connectorName)) {
    throw new AgentToolError(AGENT_TOOL_ERRORS.INVALID_INPUT, "invalid connector tool name");
  }
  let res: Response;
  try {
    res = await fetch(
      `${config.fastApiBaseUrl}/api/agent-tools/connectors/${encodeURIComponent(connectorName)}?novel_id=${encodeURIComponent(String(runNovelId))}`,
      {
        method: "POST",
        headers: { "content-type": "application/json", authorization: auth },
        body: JSON.stringify(params ?? {}),
        signal: ctrl,
      },
    );
  } catch (err) {
    if (runSignal.aborted) throw err;
    const reason = (err as Error)?.name ?? "";
    if (reason === "TimeoutError" || reason === "AbortError") {
      throw new AgentToolError(AGENT_TOOL_ERRORS.TIMEOUT, "connector tool timed out");
    }
    throw new AgentToolError(AGENT_TOOL_ERRORS.UPSTREAM_ERROR, "connector tool network error");
  }
  const bodyText = await res.text();
  if (bodyText.length > TOOL_OUTPUT_BYTE_LIMIT) {
    throw new AgentToolError(AGENT_TOOL_ERRORS.OUTPUT_TOO_LARGE, "connector response exceeds byte limit");
  }
  if (!res.ok) throw mapFacadeError(res.status, bodyText);
  return { content: [{ type: "text", text: bodyText }], details: {} };
}
