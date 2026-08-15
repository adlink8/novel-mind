import { describe, it, expect } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadAllowlistedSkills,
  loadSkillFromManifest,
  loadSkill,
  skillInstructions,
  validateRunInput,
  SkillLoadError,
  SkillValidationError,
} from "../src/skills/loader.js";

/**
 * skills-loader.test.ts（25.2-05 Task 3）：
 * - fail-closed 矩阵：未知工具 / 坏 YAML / 缺 D-09 字段 / 非法 schema 文件 / input 不匹配
 * - 正向：真实 answer-reading-question 资产加载成功，allowed_tools 排除 get_narrative_memory
 * 负向用例用临时 fixture 技能目录；不真连 backend。
 */

/** 一个 D-09 全字段合法的 skill.yaml 模板。 */
function goodYaml(name = "fixture-skill"): string {
  return `name: ${name}
version: 1.0.0
description: fixture skill
allowed_tools:
  - get_novel
  - get_chapter
read_permissions:
  - canon
write_permissions: []
forbidden_spaces:
  - canon:original
budget:
  max_calls: 10
  max_input_tokens: 1000
approval_required_for: []
input_schema: ./input.schema.json
output_schema: ./output.schema.json
`;
}

/** 在临时根下创建一个 fixture 技能目录，返回技能根路径。 */
function writeFixtureSkill(name: string, yamlText: string): string {
  const root = mkdtempSync(join(tmpdir(), "nm-loader-fixture-"));
  const dir = join(root, name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "skill.yaml"), yamlText);
  writeFileSync(join(dir, "SKILL.md"), "# Fixture Skill\n\nfixture 指令：先查证再作答。\n");
  writeFileSync(
    join(dir, "input.schema.json"),
    JSON.stringify({
      type: "object",
      required: ["question"],
      properties: { question: { type: "string" }, novel_id: { type: "integer" } },
      additionalProperties: false,
    }),
  );
  writeFileSync(
    join(dir, "output.schema.json"),
    JSON.stringify({
      type: "object",
      required: ["answer"],
      properties: { answer: { type: "string" } },
      additionalProperties: false,
    }),
  );
  return root;
}

function loadFixtureSkill(name: string, root: string) {
  return loadSkill(name, root, [name]);
}

describe("skill loader（fail-closed 矩阵）", () => {
  it("服务端 declarative_only manifest 可在无本地目录时构造内存 LoadedSkill", () => {
    const skill = loadSkillFromManifest({
      owner_id: 7,
      novel_id: 11,
      skill_version_id: 19,
      name: "chapter-notes",
      version: "1.0.0",
      description: "fixture",
      prompt: "Use only supplied evidence.",
      execution_mode: "declarative_only",
      allowed_tools: ["get_novel"],
      read_permissions: ["canon"],
      write_permissions: [],
      forbidden_spaces: ["canon:original"],
      budget: { max_tokens: 800 },
      approval_required_for: [],
      input_schema: {
        type: "object",
        required: ["question"],
        properties: { question: { type: "string" } },
        additionalProperties: false,
      },
      output_schema: {
        type: "object",
        required: ["notes"],
        properties: { notes: { type: "array" } },
        additionalProperties: false,
      },
      checksum: "359f2f238aa8360ddff3f68a6fcfa6cd4e925c58f4b1201b57475df44647ab96",
    });

    expect(skill.name).toBe("chapter-notes");
    expect(skill.instructions).toBe("Use only supplied evidence.");
    expect(skill.filePath).toMatch(/^db:\/\/owner-7\/novel-11\/skill-version-19\//);
    expect(() => validateRunInput(skill, { question: "整理" })).not.toThrow();
    expect(() => validateRunInput(skill, { question: 42 })).toThrowError(SkillValidationError);
  });

  it("服务端 manifest checksum 不匹配时 fail-closed", () => {
    expect(() => loadSkillFromManifest({
      owner_id: 7,
      novel_id: 11,
      skill_version_id: 19,
      name: "chapter-notes",
      version: "1.0.0",
      description: "fixture",
      prompt: "Use only supplied evidence.",
      execution_mode: "declarative_only",
      allowed_tools: ["get_novel"],
      read_permissions: [],
      write_permissions: [],
      forbidden_spaces: [],
      budget: {},
      approval_required_for: [],
      input_schema: { type: "object" },
      output_schema: { type: "object" },
      checksum: "0".repeat(64),
    })).toThrow(/checksum|校验和/i);
  });

  it("显式 skill 名称必须是安全 slug 且来自激活 allowlist", () => {
    expect(() => loadSkill("../answer-reading-question")).toThrow(/安全 slug/);
    expect(() => loadSkill("not-activated")).toThrow(/激活|allowlist/);

    const root = writeFixtureSkill("manifest-name", goodYaml("different-name"));
    expect(() => loadFixtureSkill("manifest-name", root)).toThrow(/name|目录|manifest/);
  });

  it("input/output schema 路径必须是 skill 目录内的相对路径，拒绝绝对路径和 ..", () => {
    const absoluteRoot = writeFixtureSkill("absolute-schema", goodYaml("absolute-schema"));
    const absoluteSchemaPath = join(absoluteRoot, "absolute-schema", "input.schema.json").replaceAll("\\", "/");
    writeFileSync(
      join(absoluteRoot, "absolute-schema", "skill.yaml"),
      goodYaml("absolute-schema").replace("input_schema: ./input.schema.json", `input_schema: "${absoluteSchemaPath}"`),
    );
    expect(() => loadFixtureSkill("absolute-schema", absoluteRoot)).toThrow(/绝对路径|相对路径/);

    const traversalRoot = writeFixtureSkill("traversal-schema", goodYaml("traversal-schema"));
    writeFileSync(
      join(traversalRoot, "traversal-schema", "skill.yaml"),
      goodYaml("traversal-schema").replace("input_schema: ./input.schema.json", "input_schema: ../outside.schema.json"),
    );
    expect(() => loadFixtureSkill("traversal-schema", traversalRoot)).toThrow(/\.\.|路径/);
  });

  it("skill 目录和 schema 路径不得通过 symlink 逃出 skills root", () => {
    const escapedSkillSource = writeFixtureSkill("escaped-skill", goodYaml("escaped-skill"));
    const skillRoot = mkdtempSync(join(tmpdir(), "nm-loader-symlink-root-"));
    symlinkSync(
      join(escapedSkillSource, "escaped-skill"),
      join(skillRoot, "escaped-skill"),
      "junction",
    );
    expect(() => loadFixtureSkill("escaped-skill", skillRoot)).toThrow(/逃出 skills root/);

    const linkedSchemaRoot = writeFixtureSkill("linked-schema", goodYaml("linked-schema"));
    const schemaSource = mkdtempSync(join(tmpdir(), "nm-loader-symlink-schema-"));
    writeFileSync(
      join(schemaSource, "input.schema.json"),
      JSON.stringify({ type: "object", properties: { question: { type: "string" } } }),
    );
    symlinkSync(
      schemaSource,
      join(linkedSchemaRoot, "linked-schema", "linked"),
      "junction",
    );
    writeFileSync(
      join(linkedSchemaRoot, "linked-schema", "skill.yaml"),
      goodYaml("linked-schema").replace("input_schema: ./input.schema.json", "input_schema: ./linked/input.schema.json"),
    );
    expect(() => loadFixtureSkill("linked-schema", linkedSchemaRoot)).toThrow(/逃出 skills root/);
  });

  it("真实 answer-reading-question 资产加载成功；allowed_tools 排除 get_narrative_memory", () => {
    const skill = loadSkill("answer-reading-question");
    expect(skill.name).toBe("answer-reading-question");
    expect(skill.version).toBe("1.0.0");
    expect(skill.allowedTools).not.toContain("get_narrative_memory");
    expect(skill.allowedTools).toContain("get_chapter");
    expect(skill.allowedTools).toHaveLength(6);
    expect(skill.instructions.length).toBeGreaterThan(0);
    // 暴露 SKILL.md 指令供确定性注入
    expect(skillInstructions(skill)).toContain("Question");
  });

  it("loadAllowlistedSkills 加载恰为 allowlisted 技能集（26 + 27 + 28 + 29 + 30 + 31 + 32 + 33 + 34 + 35 + 36 + 37 + 38 + 39 十五个技能）", () => {
    const skills = loadAllowlistedSkills();
    expect(skills).toHaveLength(15);
    expect(skills.map((s) => s.name).sort()).toEqual([
      "analyze-chapter",
      "answer-reading-question",
      "build-story-arc",
      "build-visual-bible",
      "compile-scene-spec",
      "continue-derivative-story",
      "create-canon-fork",
      "detect-key-scenes",
      "edit-derivative-story",
      "evaluate-reading-skill-runs",
      "illustrate-derivative-scene",
      "illustrate-scene",
      "prepare-export",
      "propose-illustration-anchor",
      "propose-world-model-candidates",
    ]);
  });

  it("allowed_tools 含未知域工具 → fail-closed，错误消息指名工具", () => {
    const root = writeFixtureSkill(
      "bad-tool",
      goodYaml("bad-tool").replace("  - get_novel", "  - bash"),
    );
    expect(() => loadFixtureSkill("bad-tool", root)).toThrowError(
      expect.objectContaining({ name: "SkillLoadError" }),
    );
    try {
      loadFixtureSkill("bad-tool", root);
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(SkillLoadError);
      expect((err as Error).message).toContain("bash");
    }
  });

  it("skill.yaml 非合法 YAML → fail-closed", () => {
    const root = writeFixtureSkill("bad-yaml", "name: [unclosed\n  version:");
    expect(() => loadFixtureSkill("bad-yaml", root)).toThrowError(SkillLoadError);
  });

  it("缺任一 D-09 必需字段 → fail-closed，错误消息指名字段", () => {
    const yaml = goodYaml("missing-field").replace(/^approval_required_for: \[\]$/m, "");
    const root = writeFixtureSkill("missing-field", yaml);
    try {
      loadFixtureSkill("missing-field", root);
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(SkillLoadError);
      expect((err as Error).message).toContain("approval_required_for");
    }
  });

  it("input.schema.json 不是合法 JSON Schema → fail-closed", () => {
    const root = writeFixtureSkill("bad-schema", goodYaml("bad-schema"));
    const schemaPath = join(root, "bad-schema", "input.schema.json");
    writeFileSync(schemaPath, JSON.stringify({ type: 12345 })); // 非法 JSON Schema
    expect(() => loadFixtureSkill("bad-schema", root)).toThrowError(/不是合法 JSON Schema/);
  });

  it("schema 文件不可解析（非 JSON）→ fail-closed", () => {
    const root = writeFixtureSkill("not-json", goodYaml("not-json"));
    const schemaPath = join(root, "not-json", "input.schema.json");
    writeFileSync(schemaPath, "this is not json {");
    expect(() => loadFixtureSkill("not-json", root)).toThrowError(/不可解析/);
  });

  it("运行输入不合规 → validateRunInput 在任意会话/工具调用前抛 SkillValidationError", () => {
    const skill = loadSkill("answer-reading-question");
    // 真实 skill 的 input.schema 要求 question + novel_id
    expect(() => validateRunInput(skill, { novel_id: 1 })).toThrowError(SkillValidationError);
    expect(() => validateRunInput(skill, { question: 42, novel_id: 1 })).toThrowError(
      SkillValidationError,
    );
  });

  it("运行输入合规 → validateRunInput 放行", () => {
    const root = writeFixtureSkill("ok-input", goodYaml("ok-input"));
    const skill = loadFixtureSkill("ok-input", root);
    expect(() => validateRunInput(skill, { question: "谁？" })).not.toThrow();
  });

  it("缺 SKILL.md → fail-closed（指令无法注入）", () => {
    const root = writeFixtureSkill("no-instructions", goodYaml("no-instructions"));
    rmSync(join(root, "no-instructions", "SKILL.md"));
    expect(() => loadFixtureSkill("no-instructions", root)).toThrowError(/SKILL.md/);
  });
});
