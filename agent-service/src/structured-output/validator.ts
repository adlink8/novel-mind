/**
 * Strict post-repair validator（26-06 / REQ-AGENT-08 / D-16）。
 *
 * 运行在共享 normalizer 之后：
 *   1. 只有 status=ok 且携带可重放 hash 的 normalized payload 才继续。
 *   2. 用严格 JSON Schema（additionalProperties:false 由调用方 schema 契约保证）
 *      校验 repaired payload。
 *   3. 重放校验 raw_hash / repaired_hash（stale hash → blocked）。
 *   4. 受保护字段门：必需的受保护字段必须存在；禁止字段不得出现；evidence 必须
 *      具备 leaf 资格（非空字符串、⊆ 冻结 manifest allowlist）。
 *   5. heuristic candidate-only 结果没有 EvidenceRef 资格 → blocked（fail closed）。
 *
 * 任何失败都以稳定 `blocked` 结果终止——不产生官方 Artifact / Revision，
 * 不触发 ApprovalRequest / Publisher / promotion / active-pointer 写入。
 *
 * 注意：冻结 manifest 的 leaf-evidence 权威校验仍由后端
 * `validate_answer_against_manifest`（reader_chat）承担；本模块做结构性与
 * allowlist 的前置门，并保持与 26-05 CitedAnswerArtifact 流程共享。
 */

import { Ajv, type ValidateFunction } from "ajv";
import { canonicalHash, PROTECTED_FIELDS, type NormalizeResult } from "./normalizer.js";

export interface ValidateOptions {
  /** 严格 JSON Schema（应含 additionalProperties:false）。 */
  schema?: Record<string, unknown>;
  /** 冻结 manifest 的 leaf evidence allowlist；提供后 evidence_refs 必须 ⊆ 它。 */
  allowedEvidenceRefs?: string[];
  /** 必需的受保护字段（缺失 → blocked）。 */
  requiredProtectedFields?: string[];
  /** 禁止出现的字段（出现 → blocked），如 authority / cutoff / fork / approval。 */
  forbiddenFields?: string[];
  /** 是否要求 cited-answer 必须有 evidence_refs（heuristic candidate 无资格）。 */
  requireEvidenceRefs?: boolean;
}

export type ValidationStatus = "valid" | "blocked";

export interface ValidationResult {
  status: ValidationStatus;
  /** 稳定 blocked 原因（error 汇总）。 */
  errors: string[];
  warnings: string[];
  /** 重放校验后的 hash（stale 时为 null）。 */
  verified_raw_hash: string | null;
  verified_repaired_hash: string | null;
}

const ajv = new Ajv({ allErrors: true, strict: false });

function block(errors: string[], warnings: string[]): ValidationResult {
  return {
    status: "blocked",
    errors,
    warnings,
    verified_raw_hash: null,
    verified_repaired_hash: null,
  };
}

/** 校验 normalization trail 的 wire 形状（轻量；完整权威在服务端适配器）。 */
function trailShapeErrors(result: NormalizeResult): string[] {
  const errors: string[] = [];
  if (!Array.isArray(result.normalization_actions)) {
    errors.push("normalization_actions must be an array");
  } else {
    for (const action of result.normalization_actions) {
      if (
        action === null ||
        typeof action !== "object" ||
        typeof (action as { path?: unknown }).path !== "string" ||
        typeof (action as { action?: unknown }).action !== "string" ||
        !("after" in action)
      ) {
        errors.push("each normalization action requires path, action and after");
        break;
      }
    }
  }
  if (!Array.isArray(result.warnings)) {
    errors.push("warnings must be an array of strings");
  }
  return errors;
}

/**
 * 严格 post-repair 校验：schema + lineage hash 重放 + 受保护字段 + leaf evidence。
 *
 * @param result  normalizer 输出（blocked → 直接 fail closed）。
 * @param options 校验门选项。
 */
export function validateNormalizedOutput(
  result: NormalizeResult,
  options: ValidateOptions = {},
): ValidationResult {
  const warnings = [...result.warnings];

  if (result.status === "blocked") {
    return block(
      [result.blocked_reason ?? "normalizer blocked the payload"],
      warnings,
    );
  }
  if (result.repaired === null || typeof result.repaired !== "object") {
    return block(["normalized payload is missing (repaired is null)"], warnings);
  }
  if (typeof result.repaired_hash !== "string" || typeof result.raw_hash !== "string") {
    return block(["normalization trail missing raw_hash/repaired_hash"], warnings);
  }

  // ── 1. hash 重放：stale raw_hash / repaired_hash → blocked。 ──
  const verifiedRawHash = canonicalHash(result.raw);
  if (verifiedRawHash !== result.raw_hash) {
    return block(["stale raw_hash: recomputed hash does not match the recorded trail"], warnings);
  }
  const verifiedRepairedHash = canonicalHash(result.repaired);
  if (verifiedRepairedHash !== result.repaired_hash) {
    return block(
      ["stale repaired_hash: recomputed hash does not match the recorded trail"],
      warnings,
    );
  }

  // ── 2. trail 形状。 ──
  const shapeErrors = trailShapeErrors(result);
  if (shapeErrors.length > 0) {
    return block(shapeErrors, warnings);
  }

  // ── 3. 严格 schema 校验。 ──
  if (options.schema) {
    const validate = ajv.compile(options.schema);
    if (!validate(result.repaired)) {
      const details = (validate.errors ?? [])
        .map((e) => `${e.instancePath} ${e.message ?? ""}`.trim())
        .slice(0, 12);
      return block([`schema validation failed: ${details.join("; ") || "unknown error"}`], warnings);
    }
  }

  const repaired = result.repaired as Record<string, unknown>;

  // ── 4. 必需受保护字段。 ──
  for (const field of options.requiredProtectedFields ?? []) {
    if (!(field in repaired) || repaired[field] === null || repaired[field] === undefined) {
      return block([`missing protected field ${field} (fail closed, no default injected)`], warnings);
    }
  }

  // ── 5. 禁止字段。 ──
  const forbidden = options.forbiddenFields ?? ["authority", "cutoff", "fork", "approval"];
  const presentForbidden = forbidden.filter((field) => field in repaired);
  if (presentForbidden.length > 0) {
    return block(
      [`protected-field synthesis blocked: ${presentForbidden.join(", ")} must not appear`],
      warnings,
    );
  }

  // ── 6. leaf evidence 资格门（cited-answer 网关）。 ──
  if (options.requireEvidenceRefs) {
    const refs = repaired.evidence_refs;
    if (!Array.isArray(refs) || refs.length === 0) {
      return block(
        ["heuristic candidate without evidence_refs is not eligible for the cited-answer gateway"],
        warnings,
      );
    }
    for (const ref of refs) {
      if (typeof ref !== "string" || ref.length === 0) {
        return block(["evidence_refs must be non-empty strings"], warnings);
      }
    }
    const allowlist = options.allowedEvidenceRefs;
    if (allowlist) {
      const outside = (refs as string[]).filter((ref) => !allowlist.includes(ref));
      if (outside.length > 0) {
        return block(
          [`evidence ref outside frozen manifest allowlist: ${outside.join(", ")}`],
          warnings,
        );
      }
    }
  }

  // ── 7. answer 块级 evidence（结构镜像 ReaderAnswerEnvelope）。 ──
  const answer = repaired.answer;
  if (answer !== null && typeof answer === "object") {
    const blocks = (answer as Record<string, unknown>).answer_blocks;
    if (Array.isArray(blocks)) {
      for (const block of blocks) {
        if (block === null || typeof block !== "object") continue;
        const refs = (block as Record<string, unknown>).evidence_refs;
        if (!Array.isArray(refs) || refs.length === 0) {
          return block(["answer block requires at least one evidence ref"], warnings);
        }
        for (const ref of refs) {
          if (typeof ref !== "string" || ref.length === 0) {
            return block(["answer block evidence refs must be non-empty strings"], warnings);
          }
        }
        if (options.allowedEvidenceRefs) {
          const outside = (refs as string[]).filter(
            (ref) => !options.allowedEvidenceRefs!.includes(ref),
          );
          if (outside.length > 0) {
            return block(
              [`answer block evidence ref outside frozen manifest allowlist: ${outside.join(", ")}`],
              warnings,
            );
          }
        }
      }
    }
  }

  return {
    status: "valid",
    errors: [],
    warnings,
    verified_raw_hash: verifiedRawHash,
    verified_repaired_hash: verifiedRepairedHash,
  };
}

/** 便捷断言辅助（26-05 消费）：失败抛错，便于 agent loop fail-closed。 */
export class StructuredOutputBlockedError extends Error {
  readonly errors: string[];
  readonly warnings: string[];

  constructor(errors: string[], warnings: string[]) {
    super(`structured output blocked: ${errors.join("; ")}`);
    this.name = "StructuredOutputBlockedError";
    this.errors = errors;
    this.warnings = warnings;
  }
}

export function assertValidStructuredOutput(
  result: NormalizeResult,
  options: ValidateOptions = {},
): NormalizeResult {
  const outcome = validateNormalizedOutput(result, options);
  if (outcome.status === "blocked") {
    throw new StructuredOutputBlockedError(outcome.errors, outcome.warnings);
  }
  return result;
}
