/**
 * Shared conservative structured-output normalizer（26-06 / REQ-AGENT-08 / D-16）。
 *
 * 模型产生的结构化输出只允许三类保守修复，全部来自**声明式契约**：
 *   1. alias            —— schema 明确声明的字段别名（canonical key → aliases）。
 *   2. enum_canonicalize —— schema 明确声明的 enum canonicalization（raw → canonical）。
 *   3. container_shape  —— 不丢失或合并信息的无歧义容器形状规范化
 *                          （单对象 → 数组；单元素数组 → 对象）。
 *
 * 不可变审计规则:
 *   - 原始模型输出 `raw` 保留为 immutable audit input（深拷贝，绝不就地修改）。
 *   - 对 raw 计算 canonical `raw_hash`；对修复后的 payload 计算 `repaired_hash`。
 *   - 每个修复记录 path / action / before / after / reason 到 `normalization_actions`。
 *   - 任意字符串相似匹配、默认值填充、类型强制转换、未知字段丢弃、嵌套字段猜测
 *     —— 全部禁止。
 *   - 受保护字段（evidence_refs / owner / cutoff / authority / branch / fork /
 *     approval）**绝不**由 normalizer 合成：修复路径不能触及它们；缺失的必需
 *     受保护字段 → blocked。外部 lineage 只允许经契约 `lineageFields` 显式声明
 *     后合并（值来自调用方/服务端，不是 normalizer 猜测）。
 *   - 任何不安全或歧义修复 → 稳定 `blocked` 结果（reason/warning 可审计），
 *     不返回可发布 payload（`repaired` 为 null）。
 *
 * heuristic candidate recall 永远保持 candidate-only：本模块不抽取任何 evidence /
 * authority 字段，也不给任何 payload 授予事实或引证资格（那是 validator 的职责）。
 */

import { createHash } from "node:crypto";

/** 受保护字段：normalizer 绝不合成；alias/enum/container 修复不得触及。 */
export const PROTECTED_FIELDS = [
  "evidence_refs",
  "owner",
  "owner_id",
  "novel_id",
  "cutoff",
  "authority",
  "branch",
  "fork",
  "approval",
  "approval_state",
] as const;

/** 修复动作类型（进入审计 trail）。 */
export type RepairActionKind =
  | "alias"
  | "alias_dedup"
  | "enum_canonicalize"
  | "container_shape"
  | "lineage_merge";

/** 一条修复记录（可审计、可重放）。 */
export interface NormalizationAction {
  /** JSON path（点分路径），如 `producing_skill` 或 `answer.answer_blocks`。 */
  path: string;
  action: RepairActionKind;
  before?: unknown;
  after: unknown;
  reason: string;
}

/** 声明式修复契约：只允许这里声明的修复；任何未声明改动都是 block。 */
export interface NormalizeContract {
  /** canonical key → 声明的别名（顶层键）。 */
  aliases?: Record<string, string[]>;
  /** 点分 path → raw enum 值 → canonical 值（映射必须单射，否则 blocked）。 */
  enumMaps?: Record<string, Record<string, string>>;
  /** 点分 path → 无歧义容器形状规范化。 */
  containerShapes?: Record<string, "wrap_array" | "unwrap_array">;
  /** canonical key → 外部 lineage 来源键（值由调用方提供，normalizer 不猜测）。 */
  lineageFields?: Record<string, string>;
  /** 修复后必须存在的字段（点分 path）；缺失 → blocked。 */
  requiredFields?: string[];
}

export type NormalizeStatus = "ok" | "blocked";

/** Normalizer 结果：blocked 时 repaired / repaired_hash 为 null（不可发布）。 */
export interface NormalizeResult {
  status: NormalizeStatus;
  /** 不可变原始模型输出（audit evidence）。 */
  raw: unknown;
  raw_hash: string;
  /** 修复后 payload；blocked 时为 null。 */
  repaired: unknown | null;
  repaired_hash: string | null;
  normalization_actions: NormalizationAction[];
  warnings: string[];
  blocked_reason: string | null;
}

/** 递归排序键的 JSON 序列化（与后端 canonical json.dumps 对齐）。 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortKeys);
  }
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(record).sort()) {
      out[key] = sortKeys(record[key]);
    }
    return out;
  }
  return value;
}

/** canonical SHA-256（与 backend artifacts.content_hash_of 的序列化口径一致）。 */
export function canonicalHash(value: unknown): string {
  const canonical = canonicalJson(value);
  return createHash("sha256").update(canonical, "utf8").digest("hex");
}

function deepEqual(a: unknown, b: unknown): boolean {
  return canonicalJson(a) === canonicalJson(b);
}

function getByPath(root: Record<string, unknown>, path: string): unknown {
  let cur: unknown = root;
  for (const part of path.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

function hasPath(root: Record<string, unknown>, path: string): boolean {
  let cur: unknown = root;
  const parts = path.split(".");
  for (let i = 0; i < parts.length; i++) {
    if (cur === null || typeof cur !== "object") return false;
    const record = cur as Record<string, unknown>;
    if (!(parts[i] in record)) return false;
    cur = record[parts[i]];
  }
  return true;
}

function setByPath(root: Record<string, unknown>, path: string, value: unknown): boolean {
  const parts = path.split(".");
  let cur: unknown = root;
  for (let i = 0; i < parts.length - 1; i++) {
    if (cur === null || typeof cur !== "object") return false;
    const record = cur as Record<string, unknown>;
    if (record[parts[i]] === null || typeof record[parts[i]] !== "object") {
      record[parts[i]] = {};
    }
    cur = record[parts[i]];
  }
  if (cur === null || typeof cur !== "object") return false;
  (cur as Record<string, unknown>)[parts[parts.length - 1]] = value;
  return true;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 校验契约自身：修复路径不得触及受保护字段；enum 映射必须单射。 */
function contractErrors(contract: NormalizeContract): string[] {
  const errors: string[] = [];
  const isProtected = (key: string) => (PROTECTED_FIELDS as readonly string[]).includes(key);

  for (const [canonical, aliases] of Object.entries(contract.aliases ?? {})) {
    if (isProtected(canonical)) {
      errors.push(`alias canonical key ${JSON.stringify(canonical)} touches a protected field`);
    }
    for (const alias of aliases) {
      if (isProtected(alias)) {
        errors.push(`alias ${JSON.stringify(alias)} touches a protected field`);
      }
    }
  }
  for (const path of Object.keys(contract.enumMaps ?? {})) {
    const leaf = path.split(".").pop() ?? path;
    if (isProtected(leaf)) {
      errors.push(`enum map path ${JSON.stringify(path)} touches a protected field`);
    }
  }
  for (const path of Object.keys(contract.containerShapes ?? {})) {
    const leaf = path.split(".").pop() ?? path;
    if (isProtected(leaf)) {
      errors.push(`container shape path ${JSON.stringify(path)} touches a protected field`);
    }
  }
  for (const [path, map] of Object.entries(contract.enumMaps ?? {})) {
    const reverse = new Map<string, string>();
    for (const [raw, canonical] of Object.entries(map)) {
      if (reverse.has(canonical)) {
        errors.push(
          `enum map ${JSON.stringify(path)} is non-unique: multiple raw values map to canonical ${JSON.stringify(canonical)}`,
        );
      }
      reverse.set(canonical, raw);
    }
  }
  return errors;
}

/**
 * 对模型原始结构化输出执行保守修复。
 *
 * @param raw      不可变原始模型 JSON（调用方不得再修改；本函数深拷贝工作）。
 * @param contract 声明式修复契约（alias / enum / container / lineage / required）。
 * @param lineage  外部 lineage（run 上下文提供的服务端权威值）；只有契约声明过的
 *                 `lineageFields` 才会被合并，normalizer 绝不猜测领域事实。
 */
export function normalizeStructuredOutput(
  raw: unknown,
  contract: NormalizeContract,
  lineage?: Record<string, unknown>,
): NormalizeResult {
  const actions: NormalizationAction[] = [];
  const warnings: string[] = [];
  const fail = (blockedReason: string): NormalizeResult => ({
    status: "blocked",
    raw,
    raw_hash: canonicalHash(raw),
    repaired: null,
    repaired_hash: null,
    normalization_actions: actions,
    warnings,
    blocked_reason: blockedReason,
  });

  if (!isPlainObject(raw)) {
    return fail("raw output must be a JSON object (blocked: cannot repair scalar/array root)");
  }

  const contractProblems = contractErrors(contract);
  if (contractProblems.length > 0) {
    return fail(`contract-invalid: ${contractProblems.join("; ")}`);
  }

  const rawHash = canonicalHash(raw);
  // 不可变 raw capture：深拷贝工作副本，raw 本身永不改动。
  const repaired: Record<string, unknown> = JSON.parse(canonicalJson(raw)) as Record<
    string,
    unknown
  >;
  // 记录由 lineage 合并加入的顶层键（受保护字段只允许经此处出现）。
  const lineageAddedKeys = new Set<string>();

  // ── 1. 声明式 alias 修复（顶层键）。 ──
  for (const [canonical, aliases] of Object.entries(contract.aliases ?? {})) {
    for (const alias of aliases) {
      if (!(alias in repaired)) continue;
      const aliasValue = repaired[alias];
      if (!(canonical in repaired)) {
        repaired[canonical] = aliasValue;
        delete repaired[alias];
        actions.push({
          path: canonical,
          action: "alias",
          before: aliasValue,
          after: aliasValue,
          reason: `declared alias ${JSON.stringify(alias)} moved to canonical ${JSON.stringify(canonical)}`,
        });
      } else if (deepEqual(repaired[alias], repaired[canonical])) {
        delete repaired[alias];
        actions.push({
          path: canonical,
          action: "alias_dedup",
          after: repaired[canonical],
          reason: `alias ${JSON.stringify(alias)} duplicates canonical ${JSON.stringify(canonical)}; dropped alias`,
        });
      } else {
        return fail(
          `alias-conflict: ${JSON.stringify(alias)} and canonical ${JSON.stringify(canonical)} both present with different values`,
        );
      }
    }
  }

  // ── 2. 声明式 lineage 合并（服务端权威值，非猜测）。 ──
  for (const [canonical, sourceKey] of Object.entries(contract.lineageFields ?? {})) {
    const sourceValue = lineage?.[sourceKey];
    if (sourceValue === undefined) {
      return fail(`lineage-missing: no external lineage value for ${JSON.stringify(canonical)} (${JSON.stringify(sourceKey)})`);
    }
    if (canonical in repaired) {
      if (!deepEqual(repaired[canonical], sourceValue)) {
        return fail(
          `lineage-conflict: ${JSON.stringify(canonical)} already present in raw with a different value`,
        );
      }
      // 一致：无改动，不记录 action。
    } else {
      repaired[canonical] = sourceValue;
      lineageAddedKeys.add(canonical);
      actions.push({
        path: canonical,
        action: "lineage_merge",
        before: undefined,
        after: sourceValue,
        reason: `declared lineage ${JSON.stringify(sourceKey)} merged into ${JSON.stringify(canonical)}`,
      });
    }
  }

  // ── 3. 声明式 enum canonicalization（单射映射）。 ──
  for (const [path, map] of Object.entries(contract.enumMaps ?? {})) {
    if (!hasPath(repaired, path)) continue;
    const value = getByPath(repaired, path);
    const canonicalize = (item: unknown): unknown => {
      if (typeof item !== "string" || !(item in map)) return item;
      return map[item];
    };
    if (Array.isArray(value)) {
      const next = value.map(canonicalize);
      const changed = next.some((item, i) => !deepEqual(item, value[i]));
      if (changed) {
        setByPath(repaired, path, next);
        actions.push({
          path,
          action: "enum_canonicalize",
          before: value,
          after: next,
          reason: `declared enum canonicalization applied to array at ${JSON.stringify(path)}`,
        });
      }
    } else {
      const next = canonicalize(value);
      if (!deepEqual(next, value)) {
        setByPath(repaired, path, next);
        actions.push({
          path,
          action: "enum_canonicalize",
          before: value,
          after: next,
          reason: `declared enum canonicalization applied at ${JSON.stringify(path)}`,
        });
      }
    }
  }

  // ── 4. 声明式无歧义容器形状规范化。 ──
  for (const [path, kind] of Object.entries(contract.containerShapes ?? {})) {
    if (!hasPath(repaired, path)) continue;
    const value = getByPath(repaired, path);
    if (kind === "wrap_array") {
      if (isPlainObject(value)) {
        setByPath(repaired, path, [value]);
        actions.push({
          path,
          action: "container_shape",
          before: value,
          after: [value],
          reason: `declared container shape: wrapped single object at ${JSON.stringify(path)} into array`,
        });
      }
      // 已是数组或缺失：无改动。
    } else if (kind === "unwrap_array") {
      if (Array.isArray(value) && value.length === 1) {
        setByPath(repaired, path, value[0]);
        actions.push({
          path,
          action: "container_shape",
          before: value,
          after: value[0],
          reason: `declared container shape: unwrapped single-item array at ${JSON.stringify(path)}`,
        });
      } else if (Array.isArray(value) && value.length > 1) {
        return fail(
          `ambiguous-container: ${JSON.stringify(path)} is an array of ${value.length} items; unwrap is not unambiguous`,
        );
      }
    }
  }

  // ── 5. 必需字段（含受保护字段）缺失 → blocked。 ──
  for (const field of contract.requiredFields ?? []) {
    if (!hasPath(repaired, field)) {
      const protectedTag = (PROTECTED_FIELDS as readonly string[]).includes(field)
        ? " (protected)"
        : "";
      return fail(`missing-required-field: ${field}${protectedTag}`);
    }
  }

  // ── 6. 受保护字段合成检查：除经 lineage 声明加入外，normalizer 不得新增任何受保护字段。 ──
  for (const field of PROTECTED_FIELDS) {
    if (!(field in repaired)) continue;
    if (field in raw) continue; // 原始已有 → 非合成。
    if (lineageAddedKeys.has(field)) continue; // 经声明 lineage 加入 → 非合成。
    return fail(`protected-field-synthesis: ${JSON.stringify(field)} was introduced without declared lineage`);
  }

  return {
    status: "ok",
    raw,
    raw_hash: rawHash,
    repaired,
    repaired_hash: canonicalHash(repaired),
    normalization_actions: actions,
    warnings,
    blocked_reason: null,
  };
}
