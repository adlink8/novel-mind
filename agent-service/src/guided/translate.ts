/**
 * guided 编号→key 翻译层（「确定性检索 + 单轮生成」改造 Slice 2）。
 *
 * guided 模式下模型只见编号证据菜单（1-based index），输出
 * claims[].evidence_indices；本模块把编号映射回真实 evidence_key——
 * 确定性的编号/key 加工全程不经模型（E2E 四模型矩阵：原始 ID/key 过模型
 * 的手是幻觉与编造的主要发生地）。
 *
 * 纪律：
 * - 越界/非整数编号 → 抛错（fail closed，poller repair 轮回喂菜单修正）；
 * - 缺失 evidence_indices 的 claim 原样保留（投影层负责 canon_fact 无证据
 *   等后续校验，各守其门）；
 * - 翻译产物与 agentic 路径模型输出同构，投影层零改动复用。
 */

type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * 把模型 JSON 文本里的 evidence_indices 翻译为 evidence_keys。
 *
 * @param modelText 模型原始输出（JSON 对象文本，可 markdown fence 包裹由
 *                  下游解析器处理——这里只接受纯 JSON，fail closed）。
 * @param keys      程序侧菜单（与展示给模型的编号同序，1-based）。
 */
export function translateEvidenceIndices(
  modelText: string,
  keys: readonly string[],
): string {
  const trimmed = modelText.trim();
  // 与 envelope builder 同一纪律：恰好一个完整 fence 块时接受（前后散文
  // 由 builder 再次把关）；多个块歧义，fail closed。
  const fencedMatches = [...trimmed.matchAll(/```(?:json)?\s*([\s\S]*?)```/g)];
  const candidate = fencedMatches.length === 1 ? fencedMatches[0][1].trim() : trimmed;
  let parsed: unknown;
  try {
    parsed = JSON.parse(candidate);
  } catch {
    throw new Error("guided-translate: model output is not valid JSON (fail closed)");
  }
  if (!isObject(parsed)) {
    throw new Error("guided-translate: model output must be a JSON object");
  }
  const visualBible = parsed.visual_bible;
  if (!isObject(visualBible)) {
    // 非 visual_bible 内容（缺顶层键）原样抛出给下游报错，保持错误语义一致。
    return JSON.stringify(parsed);
  }
  const claims = Array.isArray(visualBible.claims) ? visualBible.claims : [];
  const translatedClaims = claims.map((raw) => {
    if (!isObject(raw)) return raw;
    const indices = raw.evidence_indices;
    if (indices === undefined) return raw;
    if (!Array.isArray(indices)) {
      throw new Error(
        "guided-translate: evidence_indices must be an array (fail closed)",
      );
    }
    const evidenceKeys = indices.map((index) => {
      if (typeof index !== "number" || !Number.isInteger(index)) {
        throw new Error(
          `guided-translate: evidence index ${String(index)} is not an integer (fail closed)`,
        );
      }
      if (index < 1 || index > keys.length) {
        throw new Error(
          `guided-translate: evidence index ${index} out of range 1..${keys.length} (fail closed)`,
        );
      }
      return keys[index - 1];
    });
    const { evidence_indices: _dropped, ...rest } = raw;
    return { ...rest, evidence_keys: evidenceKeys };
  });
  return JSON.stringify({ ...parsed, visual_bible: { ...visualBible, claims: translatedClaims } });
}
