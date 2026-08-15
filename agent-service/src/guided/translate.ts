/**
 * guided 编号→key 翻译层（「确定性检索 + 单轮生成」改造 Slice 2）。
 *
 * guided 模式下模型只见编号证据菜单（1-based index），输出里的编号引用由
 * 本模块映射回真实 evidence_key——确定性的编号/key 加工全程不经模型
 * （E2E 四模型矩阵：原始 ID/key 过模型的手是幻觉与编造的主要发生地）。
 *
 * 每个 guided skill 一个翻译器（声明式注册）：
 * - build-visual-bible：visual_bible.claims[].evidence_indices → evidence_keys
 * - detect-key-scenes：scene_candidate_set.candidates[].evidence_indices →
 *   evidence_key（每候选恰好 1 条，多了 fail closed）
 * - propose-world-model-candidates：candidates.claims[].evidence_indices →
 *   evidence_refs（字符串 key 数组）
 *
 * 纪律：越界/非整数编号 → 抛错（fail closed，poller repair 轮回喂菜单修正）；
 * 缺失编号字段的项原样保留（投影层负责后续校验，各守其门）；翻译产物与
 * agentic 路径模型输出同构，投影层零改动复用。
 */

type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 编号 → key 的公共映射（fail closed）。 */
function mapIndices(indices: unknown, keys: readonly string[]): string[] {
  if (!Array.isArray(indices)) {
    throw new Error(
      "guided-translate: evidence_indices must be an array (fail closed)",
    );
  }
  return indices.map((index) => {
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
}

/** 对列表项应用 evidence_indices 翻译；无编号字段的项原样保留。 */
function translateItems(
  items: unknown,
  keys: readonly string[],
  apply: (rest: JsonObject, mapped: string[]) => JsonObject,
): unknown {
  if (!Array.isArray(items)) return items;
  return items.map((raw) => {
    if (!isObject(raw)) return raw;
    const indices = raw.evidence_indices;
    if (indices === undefined) return raw;
    const mapped = mapIndices(indices, keys);
    const { evidence_indices: _dropped, ...rest } = raw;
    return apply(rest, mapped);
  });
}

type Translator = (root: JsonObject, keys: readonly string[]) => JsonObject;

const TRANSLATORS: Record<string, Translator> = {
  "build-visual-bible": (root, keys) => {
    const visualBible = root.visual_bible;
    if (!isObject(visualBible)) return root;
    return {
      ...root,
      visual_bible: {
        ...visualBible,
        claims: translateItems(visualBible.claims, keys, (rest, mapped) => ({
          ...rest,
          evidence_keys: mapped,
        })),
      },
    };
  },
  "detect-key-scenes": (root, keys) => {
    const set = root.scene_candidate_set;
    if (!isObject(set)) return root;
    return {
      ...root,
      scene_candidate_set: {
        ...set,
        candidates: translateItems(set.candidates, keys, (rest, mapped) => {
          if (mapped.length !== 1) {
            throw new Error(
              "guided-translate: each scene candidate requires exactly one evidence index (fail closed)",
            );
          }
          return { ...rest, evidence_key: mapped[0] };
        }),
      },
    };
  },
  "propose-world-model-candidates": (root, keys) => {
    const candidates = root.candidates;
    if (!isObject(candidates)) return root;
    return {
      ...root,
      candidates: {
        ...candidates,
        claims: translateItems(candidates.claims, keys, (rest, mapped) => ({
          ...rest,
          evidence_refs: mapped,
        })),
      },
    };
  },
};

/**
 * 把模型 JSON 文本里的 evidence_indices 翻译为真实 evidence key。
 *
 * @param modelText 模型原始输出（纯 JSON 或恰好一个 markdown fence 块）。
 * @param keys      程序侧菜单（与展示给模型的编号同序，1-based）。
 * @param skillName guided skill 名（决定翻译器；未注册 → 原样返回由下游报错）。
 */
export function translateEvidenceIndices(
  modelText: string,
  keys: readonly string[],
  skillName: string,
): string {
  const trimmed = modelText.trim();
  // 与 envelope builder 同一纪律：恰好一个完整 fence 块时接受；多个块歧义，
  // fail closed。
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
  const translator = TRANSLATORS[skillName];
  if (!translator) {
    return JSON.stringify(parsed);
  }
  return JSON.stringify(translator(parsed, keys));
}
