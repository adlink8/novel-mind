/**
 * MCP 结果 → external_evidence D-09 信封映射（25.3-03 / D-09 / T-25.3-03-02）。
 *
 * 外部（MCP）结果只能物化为 external_evidence artifact；prohibited_from_canon 是
 * 本映射器内硬编码的 true 常量——任何调用方参数都无法翻转它（服务端常量，
 * 与 25.2-03 CitedAnswerArtifact 信封纪律一致，永不与 original_text_evidence 混淆）。
 */

export type ExternalEvidenceConfidence = "low" | "medium" | "high";

/** D-09 来源条目：retrieved_from 恒为 "mcp"。 */
export interface ExternalEvidenceSource {
  server: string;
  tool: string;
  uri: string;
  title: string;
  retrieved_from: "mcp";
}

/** D-09 主张条目：text + 在来源结果中的下标。 */
export interface ExternalEvidenceClaim {
  text: string;
  source_index: number;
}

/** D-09 信封（backend schemas/agent_runtime.py ExternalEvidenceArtifact 逐字段镜像）。 */
export interface ExternalEvidenceEnvelope {
  type: "external_evidence";
  schema_version: 1;
  sources: ExternalEvidenceSource[];
  retrieval_time: string; // ISO-8601
  claims: ExternalEvidenceClaim[];
  confidence: ExternalEvidenceConfidence;
  /** 服务端常量 true——任何调用方参数都不能把它翻成 false。 */
  prohibited_from_canon: true;
  release_status: "external";
}

/** 映射选项：来源 uri/title 与置信度（默认 low）。 */
export interface ToExternalEvidenceOptions {
  uri?: string;
  title?: string;
  confidence?: ExternalEvidenceConfidence;
}

function extractClaims(result: unknown): ExternalEvidenceClaim[] {
  let items: unknown[];
  if (Array.isArray(result)) {
    items = result;
  } else if (result !== null && typeof result === "object") {
    const content = (result as { content?: unknown }).content;
    items = Array.isArray(content) ? content : [result];
  } else {
    items = result === undefined || result === null ? [] : [result];
  }
  return items.map((item, index) => ({
    text:
      typeof item === "string"
        ? item
        : item !== null && typeof item === "object" && typeof (item as { text?: unknown }).text === "string"
          ? ((item as { text: string }).text as string)
          : JSON.stringify(item),
    source_index: index,
  }));
}

/** 从结果首项提取可选的 uri/title 元数据（外部服务器常在结果内返回来源 URL）。 */
function firstItemMetadata(result: unknown): { uri?: string; title?: string } {
  const first = Array.isArray(result) ? result[0] : result;
  if (first === null || typeof first !== "object") return {};
  const record = first as { uri?: unknown; title?: unknown };
  return {
    uri: typeof record.uri === "string" ? record.uri : undefined,
    title: typeof record.title === "string" ? record.title : undefined,
  };
}

/**
 * 把一次 MCP 工具结果映射为 D-09 external_evidence 信封。
 *
 * @param serverName MCP 服务器名（allowlist 内名称）
 * @param toolName   被调用的外部工具名
 * @param result     MCP 工具返回的原始结果（数组 / {content:[…]} / 标量）
 * @param options    来源 uri/title 与置信度覆盖（缺省时尝试从结果首项元数据提取）
 */
export function toExternalEvidence(
  serverName: string,
  toolName: string,
  result: unknown,
  options: ToExternalEvidenceOptions = {},
): ExternalEvidenceEnvelope {
  const claims = extractClaims(result);
  const meta = firstItemMetadata(result);
  const uri = options.uri ?? meta.uri ?? `mcp://${serverName}/${toolName}`;
  const title = options.title ?? meta.title ?? `${serverName} external tool result`;
  return {
    type: "external_evidence",
    schema_version: 1,
    sources: [
      {
        server: serverName,
        tool: toolName,
        uri,
        title,
        retrieved_from: "mcp",
      },
    ],
    retrieval_time: new Date().toISOString(),
    claims,
    confidence: options.confidence ?? "low",
    // 服务端常量：无参数可翻转（Literal[True] 在 backend schema 亦强制）。
    prohibited_from_canon: true,
    release_status: "external",
  };
}
