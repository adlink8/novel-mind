/**
 * fetch-stream SSE 助手（25.2-04 Agent Workspace）。
 *
 * 为什么不用 EventSource：原生 EventSource 无法携带 Authorization 头（Pitfall 4），
 * 因此用 fetch + `res.body.getReader()` 手写解析（零新依赖，~50 行）：
 * - POST + `Authorization: Bearer <token>`（token 来源与 api.ts:42-45 一致）。
 * - 按 "\n\n" 分帧；跨 chunk 边界的帧缓存拼接，直到完整才派发。
 * - 每帧取 `data:` 行并 JSON.parse → onEvent；畸形帧 → onError，流继续。
 * - 非 2xx → 抛出带 status 的 Error，绝不派发任何伪造帧。
 * - abort → `DOMException("Aborted", "AbortError")`（与 pollReaderChatJob 同约定）。
 */

import { getAccessToken } from "@/lib/api";

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

/**
 * POST + 流式读取 agent run SSE。
 *
 * 约定：
 * - 非 2xx → `throw`（Error 带 `status` 字段）；响应无 body → throw。
 * - 读流过程中 abort → 以 `DOMException("Aborted", "AbortError")` reject。
 * - 只派发服务端发来的帧，绝不客户端伪造 assistant 内容。
 */
export async function streamAgentRun(
  url: string,
  body: unknown,
  { signal, onEvent, onError }: StreamAgentRunOptions
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${getAccessToken() ?? ""}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
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
        dispatchFrame(buffer.slice(0, sep), onEvent, onError);
        buffer = buffer.slice(sep + 2);
        sep = buffer.indexOf("\n\n");
      }
    }
    // 流结束后冲刷残余：最后一帧可能没有尾随 "\n\n"。
    if (buffer.trim()) {
      dispatchFrame(buffer, onEvent, onError);
    }
  } catch (err) {
    if (signal?.aborted || (err as Error)?.name === "AbortError") {
      throw new DOMException("Aborted", "AbortError");
    }
    throw err;
  }
}
