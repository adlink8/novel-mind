/**
 * Pi 运行时工具证据（只读摘要）。
 *
 * 事实来源只能是 Pi 写入会话的 `toolResult` 消息；assistant 的 toolCall、
 * 模型输出中的 `tool_runs` 以及任何 args/content 都不是持久化事实。
 */

export interface RuntimeToolRunSummary {
  tool_name: string;
  calls: number;
  errors: number;
}

export interface ToolEvidence {
  toolName: string;
  content: string;
}

export interface RuntimeToolEvidenceSnapshot {
  toolRuns: RuntimeToolRunSummary[];
  successfulEvidences: ToolEvidence[];
}

export class ToolEvidenceExtractionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolEvidenceExtractionError";
  }
}

interface RuntimeToolResultMessage {
  role?: string;
  toolName?: unknown;
  isError?: unknown;
  content?: unknown;
}

function textContent(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .filter(
      (block): block is { type?: unknown; text?: unknown } =>
        block !== null && typeof block === "object" && !Array.isArray(block),
    )
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => block.text as string)
    .join("")
    // Tool facade already enforces a 64 KiB response ceiling. Keep the complete
    // chapter result in ephemeral runtime memory so deterministic lineage
    // hashes represent the actual chapter instead of a 2 KiB prefix.
    .slice(0, 65_536);
}

/**
 * 从 Pi 会话 transcript 生成确定性运行时摘要。
 *
 * - 只处理 `role=toolResult`；因此模型声称的 `tool_runs`/toolCall 不会计入；
 * - tool_name 必须属于当前 Skill 的 allowed_tools，越界立即 fail closed；
 * - 摘要按 tool_name 字典序排列，避免并行工具完成顺序影响审计结果；
 * - 返回的 toolRuns 不含 args、结果正文或错误正文。
 */
export function extractRuntimeToolEvidence(
  messages: readonly unknown[],
  allowedTools: readonly string[],
): RuntimeToolEvidenceSnapshot {
  const allowed = new Set(allowedTools);
  const counts = new Map<string, { calls: number; errors: number }>();
  const successfulEvidences: ToolEvidence[] = [];

  for (const value of messages) {
    const message = (value ?? {}) as RuntimeToolResultMessage;
    if (message.role !== "toolResult") continue;

    if (typeof message.toolName !== "string" || message.toolName.trim() === "") {
      throw new ToolEvidenceExtractionError(
        "runtime tool result missing tool_name",
      );
    }
    const toolName = message.toolName;
    if (!allowed.has(toolName)) {
      throw new ToolEvidenceExtractionError(
        `runtime tool ${toolName} is outside Skill allowed_tools`,
      );
    }
    if (typeof message.isError !== "boolean") {
      throw new ToolEvidenceExtractionError(
        `runtime tool ${toolName} has invalid isError flag`,
      );
    }

    const current = counts.get(toolName) ?? { calls: 0, errors: 0 };
    current.calls += 1;
    if (message.isError) current.errors += 1;
    counts.set(toolName, current);

    if (!message.isError) {
      const content = textContent(message.content);
      if (content) successfulEvidences.push({ toolName, content });
    }
  }

  const toolRuns = [...counts.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([tool_name, count]) => ({
      tool_name,
      calls: count.calls,
      errors: count.errors,
    }));

  return { toolRuns, successfulEvidences };
}
