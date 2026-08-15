/**
 * 域工具参数 schema 与后端请求契约的漂移门禁（P1a 修复的系统性底座缺陷）。
 *
 * 后端 ``backend/app/schemas/agent_tools.py`` 的 ``*Request`` 模型全部是
 * ``extra="forbid"``：模型按 TS 工具定义里** advertised 的参数名**调用时，
 * 任何后端不认识的字段都 → 422（E2E 实测：search_novel_text 23/27 次 422，
 * 模型烧光 max_calls 预算在重试上）。
 *
 * 本测试把每个域工具的 TS 参数键钉在后端请求字段集上（唯一事实源 =
 * 后端 *Request.model_fields；``novel_id`` 例外——客户端剥离并经查询参数
 * 注入 run 绑定值）。后端字段集变化时必须有意识地更新本 pin。
 */

import { describe, it, expect } from "vitest";
import { buildReadingTools } from "../src/tools/domain/reading.js";
import { buildWorldModelTools } from "../src/tools/domain/world-model.js";
import { buildVisualTools } from "../src/tools/domain/visual.js";

// 后端 backend/app/schemas/agent_tools.py 请求模型字段（extra="forbid"）。
// novel_id 由 fastapiToolCall 剥离并经 ?novel_id= 注入，不出现在请求体。
const BACKEND_REQUEST_FIELDS: Record<string, string[]> = {
  get_novel: [],
  get_chapter: ["chapter_id"],
  search_novel_text: ["mode", "query", "top_k"],
  get_timeline: ["causal", "chapter_end", "chapter_start", "ordering", "person"],
  get_relationships: [
    "character_id",
    "include_provisional",
    "relation_type",
    "source",
    "through_chapter",
    "version_id",
  ],
  get_clues: ["character_id", "status"],
  get_narrative_memory: ["through_chapter", "version_id", "view"],
  get_events: ["cutoff", "version_id"],
  get_character_state: ["cutoff", "pov", "subject", "version_id"],
  get_character_knowledge: ["cutoff", "pov", "subject", "version_id"],
  get_world_rules: ["cutoff", "version_id"],
  get_evidence_span: ["chapter_id", "chunk_id", "content_hash", "source_end", "source_start"],
  get_visual_bible: ["approved_only", "version_id"],
  generate_image_candidate: [
    "height",
    "job_key",
    "model",
    "prompt_revision_id",
    "provider",
    "width",
  ],
};

interface ToolLike {
  name: string;
  parameters: { properties?: Record<string, unknown> };
}

function toolParamKeys(tools: ToolLike[]): Record<string, string[]> {
  return Object.fromEntries(
    tools.map((tool) => [
      tool.name,
      Object.keys(tool.parameters.properties ?? {}).sort(),
    ]),
  );
}

describe("域工具参数键 ⊆ 后端请求字段（extra=forbid 漂移门）", () => {
  const all: ToolLike[] = [
    ...(buildReadingTools("auth", 1) as unknown as ToolLike[]),
    ...(buildWorldModelTools("auth", 1) as unknown as ToolLike[]),
    ...(buildVisualTools("auth", 1) as unknown as ToolLike[]),
  ];
  const actual = toolParamKeys(all);

  for (const [name, backendFields] of Object.entries(BACKEND_REQUEST_FIELDS)) {
    it(`${name} 不 advertised 后端不认识的参数`, () => {
      const keys = (actual[name] ?? []).filter((key) => key !== "novel_id");
      const allowed = new Set(backendFields);
      const extra = keys.filter((key) => !allowed.has(key));
      expect(extra, `${name} advertised 了后端 forbid 的参数: ${extra}`).toEqual([]);
    });
  }

  it("pin 覆盖全部已构建工具（防漏钉）", () => {
    expect(Object.keys(actual).sort()).toEqual(
      Object.keys(BACKEND_REQUEST_FIELDS).sort(),
    );
  });
});
