import { describe, it, expect } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadAllowlistedSkills,
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

describe("skill loader（fail-closed 矩阵）", () => {
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

  it("loadAllowlistedSkills 加载恰为 allowlisted 技能集（26 + 27 + 28 + 29 + 30 + 31 + 32 + 33 + 34 十个技能）", () => {
    const skills = loadAllowlistedSkills();
    expect(skills).toHaveLength(10);
    expect(skills.map((s) => s.name).sort()).toEqual([
      "analyze-chapter",
      "answer-reading-question",
      "build-story-arc",
      "build-visual-bible",
      "compile-scene-spec",
      "detect-key-scenes",
      "evaluate-reading-skill-runs",
      "illustrate-scene",
      "propose-illustration-anchor",
      "propose-world-model-candidates",
    ]);
  });

  it("allowed_tools 含未知域工具 → fail-closed，错误消息指名工具", () => {
    const root = writeFixtureSkill(
      "bad-tool",
      goodYaml("bad-tool").replace("  - get_novel", "  - bash"),
    );
    expect(() => loadSkill("bad-tool", root)).toThrowError(
      expect.objectContaining({ name: "SkillLoadError" }),
    );
    try {
      loadSkill("bad-tool", root);
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(SkillLoadError);
      expect((err as Error).message).toContain("bash");
    }
  });

  it("skill.yaml 非合法 YAML → fail-closed", () => {
    const root = writeFixtureSkill("bad-yaml", "name: [unclosed\n  version:");
    expect(() => loadSkill("bad-yaml", root)).toThrowError(SkillLoadError);
  });

  it("缺任一 D-09 必需字段 → fail-closed，错误消息指名字段", () => {
    const yaml = goodYaml("missing-field").replace(/^approval_required_for: \[\]$/m, "");
    const root = writeFixtureSkill("missing-field", yaml);
    try {
      loadSkill("missing-field", root);
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
    expect(() => loadSkill("bad-schema", root)).toThrowError(/不是合法 JSON Schema/);
  });

  it("schema 文件不可解析（非 JSON）→ fail-closed", () => {
    const root = writeFixtureSkill("not-json", goodYaml("not-json"));
    const schemaPath = join(root, "not-json", "input.schema.json");
    writeFileSync(schemaPath, "this is not json {");
    expect(() => loadSkill("not-json", root)).toThrowError(/不可解析/);
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
    const skill = loadSkill("ok-input", root);
    expect(() => validateRunInput(skill, { question: "谁？" })).not.toThrow();
  });

  it("缺 SKILL.md → fail-closed（指令无法注入）", () => {
    const root = writeFixtureSkill("no-instructions", goodYaml("no-instructions"));
    rmSync(join(root, "no-instructions", "SKILL.md"));
    expect(() => loadSkill("no-instructions", root)).toThrowError(/SKILL.md/);
  });
});
