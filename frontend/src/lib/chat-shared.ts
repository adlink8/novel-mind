import type { GenerationJobView } from "@/lib/api";

/** 生成新的客户端消息 id（reader-chat-panel 与 analysis 对话面板共用）。 */
export function newClientMessageId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `cm-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 生成任务状态展示文案（reader-chat-panel 与 analysis 对话面板共用）。 */
export function jobStatusLabel(
  job: GenerationJobView | null | undefined
): string | null {
  if (!job) return null;
  const reason = (job.status_reason || job.error_code || "").trim();
  switch (job.status) {
    case "queued":
      return "排队中…";
    case "running":
      return "生成中…";
    case "paused_budget":
      return reason ? `预算已暂停：${reason}` : "预算已暂停";
    case "paused_dependency":
      return reason ? `模型/依赖不可用：${reason}` : "模型或依赖不可用（可点重试）";
    case "cancelled":
      return "已取消";
    case "failed":
    case "failed_validation":
      return job.error_code ? `失败：${job.error_code}` : "生成失败";
    case "completed":
      return null;
    default:
      return job.status;
  }
}
