/**
 * skill.yaml fail-closed loader（25.2-05 / D-09 / Pitfall 3）。
 *
 * 每个技能目录 `src/skills/<name>/` 按 D-09 契约加载：skill.yaml 用 ajv 对
 * meta-schema 校验（10 个必需字段），`allowed_tools` 每一项必须落在
 * `DOMAIN_TOOL_NAMES`（registry 单一事实源）内，`input.schema.json` /
 * `output.schema.json` 必须是合法 JSON Schema（ajv 编译通过）。任一失败 →
 * 技能绝不注册（fail-closed），错误指名字段/工具。
 *
 * 技能指令（SKILL.md）由 `skillInstructions()` 返回，会话工厂确定性注入
 * systemPrompt —— 绝不靠 Pi skill discovery（read 工具已被 noTools:"all" 禁用，
 * Pitfall 3）。
 */

import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { Ajv, type ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import { DOMAIN_TOOL_NAMES } from "../tools/registry.js";

/** D-09 必需字段（与 backend app.schemas.agent_runtime.SkillVersionRegister 镜像）。 */
export const D09_FIELDS = [
  "name",
  "version",
  "allowed_tools",
  "read_permissions",
  "write_permissions",
  "forbidden_spaces",
  "budget",
  "approval_required_for",
  "input_schema",
  "output_schema",
] as const;

/** 默认技能根目录：`src/skills`（相对本文件）。 */
const DEFAULT_SKILLS_ROOT = fileURLToPath(new URL("../skills", import.meta.url));

/** 25.2 允许加载的技能目录集（单一 allowlist；27-05 加入世界模型技能，28-05 加入叙事记忆技能，29-05 加入评估技能，30-05 加入 Visual Bible 技能，31-04 加入关键场景检测技能，32-05 加入 Scene Spec/Prompt 编译技能，33-05 加入插图生成技能）。 */
export const ALLOWLISTED_SKILL_DIRS = [
  "answer-reading-question",
  "propose-world-model-candidates",
  "analyze-chapter",
  "build-story-arc",
  "evaluate-reading-skill-runs",
  "build-visual-bible",
  "detect-key-scenes",
  "compile-scene-spec",
  "illustrate-scene",
] as const;

/** 加载失败（缺字段/未知工具/坏 schema），fail-closed，错误信息指名根因。 */
export class SkillLoadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SkillLoadError";
  }
}

/** 运行输入/输出校验失败（server 据此返回 422）。 */
export class SkillValidationError extends Error {
  readonly errors: string[];

  constructor(skillName: string, kind: "input" | "output", errors: string[]) {
    super(`skill ${skillName} ${kind} 校验失败: ${errors.join("; ")}`);
    this.name = "SkillValidationError";
    this.errors = errors;
  }
}

/** 已加载校验的技能（fail-closed 通过后才存在）。 */
export interface LoadedSkill {
  name: string;
  version: string;
  description: string;
  allowedTools: string[];
  readPermissions: string[];
  writePermissions: string[];
  forbiddenSpaces: string[];
  budget: Record<string, unknown>;
  approvalRequiredFor: string[];
  /** skill.yaml 绝对路径。 */
  filePath: string;
  /** 技能目录绝对路径。 */
  baseDir: string;
  /** SKILL.md 指令正文（注入 systemPrompt，绝不 discovery）。 */
  instructions: string;
  /** 编译好的 input/output JSON Schema 校验器。 */
  validateInput: ValidateFunction;
  validateOutput: ValidateFunction;
}

// ────────────────────────── ajv 实例（模块级单例） ──────────────────────────

const ajv = new Ajv({ allErrors: true, strict: false });
// ajv-formats 的 TS 类型为 FormatsPlugin 接口（不可直接调用），运行时为插件函数。
(addFormats as unknown as (instance: typeof ajv) => void)(ajv);

/** D-09 meta-schema：编码 10 个必需字段的类型形状（fail-closed 校验用）。 */
const D09_META_SCHEMA = {
  type: "object",
  required: [...D09_FIELDS],
  properties: {
    name: { type: "string", minLength: 1 },
    version: { type: "string", minLength: 1 },
    description: { type: "string" },
    allowed_tools: { type: "array", items: { type: "string" }, minItems: 1 },
    read_permissions: { type: "array", items: { type: "string" } },
    write_permissions: { type: "array", items: { type: "string" } },
    forbidden_spaces: { type: "array", items: { type: "string" } },
    budget: { type: "object" },
    approval_required_for: { type: "array", items: { type: "string" } },
    input_schema: { type: "string", minLength: 1 },
    output_schema: { type: "string", minLength: 1 },
  },
  additionalProperties: true,
} as const;

const validateMeta = ajv.compile(D09_META_SCHEMA);

/** 校验 skill.yaml 的 D-09 必需字段，失败返回缺失/非法字段名列表。 */
function checkD09Fields(raw: Record<string, unknown>): string[] {
  const errors: string[] = [];
  for (const field of D09_FIELDS) {
    if (!(field in raw) || raw[field] === null || raw[field] === undefined || raw[field] === "") {
      errors.push(field);
    }
  }
  if (!validateMeta(raw)) {
    for (const err of validateMeta.errors ?? []) {
      const field = String(err.instancePath ?? "").replace(/^\//, "");
      if (field && !errors.includes(field)) errors.push(field);
    }
  }
  return errors;
}

/** 读取相对 skill.yaml 的 schema 文件并校验为合法 JSON Schema。 */
function loadJsonSchema(schemaPath: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(schemaPath, "utf8"));
  } catch (err) {
    throw new SkillLoadError(`schema 文件不可解析: ${schemaPath} (${(err as Error).message})`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new SkillLoadError(`schema 文件不是 JSON Schema 对象: ${schemaPath}`);
  }
  const schema = parsed as Record<string, unknown>;
  try {
    // 编译失败 = 非法 JSON Schema → fail-closed。
    ajv.compile(schema);
  } catch (err) {
    throw new SkillLoadError(`schema 不是合法 JSON Schema: ${schemaPath} (${(err as Error).message})`);
  }
  return schema;
}

/**
 * 加载并 fail-closed 校验单个技能。
 *
 * @param name   技能名（目录名）。
 * @param root   技能根目录（默认 `src/skills`；测试可传临时 fixture 根）。
 * @returns 通过全部校验的 LoadedSkill。
 */
export function loadSkill(name: string, root: string = DEFAULT_SKILLS_ROOT): LoadedSkill {
  const baseDir = resolve(root, name);
  const skillYamlPath = join(baseDir, "skill.yaml");

  let rawText: string;
  try {
    rawText = readFileSync(skillYamlPath, "utf8");
  } catch {
    throw new SkillLoadError(`技能不存在或 skill.yaml 不可读: ${skillYamlPath}`);
  }

  let raw: Record<string, unknown>;
  try {
    const parsed = parseYaml(rawText);
    if (typeof parsed !== "object" || parsed === null) {
      throw new Error("skill.yaml 顶层必须是对象");
    }
    raw = parsed as Record<string, unknown>;
  } catch (err) {
    throw new SkillLoadError(`skill.yaml 不是合法 YAML: ${skillYamlPath} (${(err as Error).message})`);
  }

  const missing = checkD09Fields(raw);
  if (missing.length > 0) {
    throw new SkillLoadError(`skill.yaml 缺少/非法 D-09 必需字段: ${missing.join(", ")} (${skillYamlPath})`);
  }

  const allowedTools = raw.allowed_tools as string[];
  for (const tool of allowedTools) {
    if (!(DOMAIN_TOOL_NAMES as readonly string[]).includes(tool)) {
      throw new SkillLoadError(`allowed_tools 含未知域工具: ${tool} (skill ${name})`);
    }
  }

  const inputSchemaPath = resolve(baseDir, raw.input_schema as string);
  const outputSchemaPath = resolve(baseDir, raw.output_schema as string);
  const inputSchema = loadJsonSchema(inputSchemaPath);
  const outputSchema = loadJsonSchema(outputSchemaPath);

  // SKILL.md 指令：失败则 fail-closed（技能缺指令无法注入）。
  const instructionsPath = join(baseDir, "SKILL.md");
  let instructions: string;
  try {
    instructions = readFileSync(instructionsPath, "utf8");
  } catch {
    throw new SkillLoadError(`技能缺少 SKILL.md 指令: ${instructionsPath}`);
  }

  const validateInput = ajv.compile(inputSchema);
  const validateOutput = ajv.compile(outputSchema);

  return {
    name: raw.name as string,
    version: raw.version as string,
    description: typeof raw.description === "string" ? (raw.description as string) : "",
    allowedTools: [...allowedTools],
    readPermissions: [...(raw.read_permissions as string[])],
    writePermissions: [...(raw.write_permissions as string[])],
    forbiddenSpaces: [...(raw.forbidden_spaces as string[])],
    budget: { ...(raw.budget as Record<string, unknown>) },
    approvalRequiredFor: [...(raw.approval_required_for as string[])],
    filePath: skillYamlPath,
    baseDir,
    instructions,
    validateInput,
    validateOutput,
  };
}

/** 运行输入校验：不合规抛 SkillValidationError（任何会话/工具调用之前）。 */
export function validateRunInput(skill: LoadedSkill, input: unknown): void {
  if (!skill.validateInput(input)) {
    throw new SkillValidationError(
      skill.name,
      "input",
      (skill.validateInput.errors ?? []).map((e) => `${e.instancePath} ${e.message ?? ""}`.trim()),
    );
  }
}

/** 运行输出校验（agent_end stop 时 finalize 前）：不合规抛 SkillValidationError。 */
export function validateRunOutput(skill: LoadedSkill, output: unknown): void {
  if (!skill.validateOutput(output)) {
    throw new SkillValidationError(
      skill.name,
      "output",
      (skill.validateOutput.errors ?? []).map((e) => `${e.instancePath} ${e.message ?? ""}`.trim()),
    );
  }
}

/** 返回 SKILL.md 指令正文（注入 systemPrompt 用；绝不 Pi skill discovery）。 */
export function skillInstructions(skill: LoadedSkill): string {
  return skill.instructions;
}

/** 加载 25.2 允许的技能集（单一 allowlist，ResourceLoader override 的供给源）。 */
export function loadAllowlistedSkills(root: string = DEFAULT_SKILLS_ROOT): LoadedSkill[] {
  return ALLOWLISTED_SKILL_DIRS.map((name) => loadSkill(name, root));
}
