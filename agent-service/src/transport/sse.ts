/**
 * SSE transport（25.2-05 / D-19 / Pattern 5）。
 *
 * 把 Pi 会话事件序列化为精选的 SSE 帧子集（Pattern 5 curation）：
 * `message_update` → `{type:"delta",text}`；`tool_execution_start` →
 * `{type:"tool_start",toolName,args}`；`tool_execution_end` →
 * `{type:"tool_end",toolName,isError}`；`turn_end` → `{type:"turn_end",usage}`。
 * 其余 Pi 事件一律丢弃；run 终结帧（run_end）、artifact 预览帧与审批帧
 * （approval_request，25.3-06 / D-11）由 server 合成，不来自单一 Pi 事件。
 * approval_request 帧**只通知**——决策权威在 FastAPI，浏览器仅渲染（T-25.3-04-02）。
 * 每帧格式 `data: {...}\n\n`。
 */

import type { ServerResponse } from "node:http";

/** 精选 SSE 帧集合（客户端可消费的契约形状）。 */
export type SseFrame =
  | { type: "delta"; text: string }
  | { type: "tool_start"; toolName: string; args: unknown }
  | { type: "tool_end"; toolName: string; isError: boolean }
  | { type: "turn_end"; usage?: unknown }
  | { type: "run_end"; runId: string; status: "completed" | "cancelled" | "failed"; error_code?: string }
  | { type: "artifact"; artifact: unknown }
  | { type: "approval_request"; request: unknown };

/** Pi 事件类型（精选子集之外的其余事件在映射时被丢弃）。 */
type PiEventLike = {
  type: string;
  toolName?: string;
  args?: unknown;
  isError?: boolean;
  assistantMessageEvent?: unknown;
  message?: unknown;
  usage?: unknown;
};

/**
 * 纯函数：把单个 Pi 事件映射为精选 SSE 帧；不匹配的事件返回 null（丢弃）。
 * 独立可单测。
 */
export function piEventToFrame(event: PiEventLike): SseFrame | null {
  switch (event.type) {
    case "message_update": {
      // text_delta / thinking_delta 携带增量文本；无增量（如 start）丢弃。
      const ev = event.assistantMessageEvent as { type?: string; delta?: unknown } | undefined;
      if (ev && typeof ev.delta === "string") {
        return { type: "delta", text: ev.delta };
      }
      return null;
    }
    case "tool_execution_start":
      return {
        type: "tool_start",
        toolName: event.toolName ?? "unknown",
        args: event.args ?? {},
      };
    case "tool_execution_end":
      return {
        type: "tool_end",
        toolName: event.toolName ?? "unknown",
        isError: event.isError ?? false,
      };
    case "turn_end": {
      const usage = (event.message as { usage?: unknown } | undefined)?.usage ?? event.usage;
      return { type: "turn_end", usage };
    }
    default:
      // 其余 Pi 事件（agent_start/turn_start/message_start/message_end/…）丢弃。
      return null;
  }
}

/**
 * 创建 SSE 写入器：`send` 写一帧（data: JSON\n\n），`close` 结束响应。
 * 不负责会话生命周期（server 的 req.on("close") → session.abort()）。
 */
export function createSseRunStream(res: ServerResponse): {
  send(frame: SseFrame): void;
  close(): void;
} {
  return {
    send(frame) {
      res.write(`data: ${JSON.stringify(frame)}\n\n`);
    },
    close() {
      res.end();
    },
  };
}
