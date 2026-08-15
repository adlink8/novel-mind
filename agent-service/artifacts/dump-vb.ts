import { projectVisualBibleVersion } from "../src/structured-output/visual-bible-projection.js";
import { writeFileSync } from "node:fs";
const SPAN_CONTENT_HASH = "1".repeat(64);
const KEY = `qp:7:0:40:${SPAN_CONTENT_HASH}`;
const version = projectVisualBibleVersion(
  {
    style_profile: null,
    constraints: null,
    entities: [{ entity_key: "char-mu-shijing", entity_type: "character", description: "慕师靖：白狐裘、立如松。", authority: "canon_fact" }],
    claims: [
      { entity_key: "char-mu-shijing", authority: "canon_fact", description: "慕师靖入城时披白狐裘。", evidence_keys: [KEY] },
      { entity_key: "char-mu-shijing", authority: "literary_interpretation", description: "白狐裘象征她与尘世的距离。", author: "reader-agent", rationale: "文本意象解读。" },
    ],
  },
  { ownerId: 2, novelId: 6, runId: "1", sourceSnapshotHash: "b".repeat(64), cutoffChapter: 3 },
  [{ toolName: "get_evidence_span", content: JSON.stringify({ evidence_key: KEY, chapter_id: 7, chapter_number: 2, novel_id: 6, source_start: 0, source_end: 40, content_hash: SPAN_CONTENT_HASH, excerpt: "慕师靖披着白狐裘，立在城门口。" }) }],
);
writeFileSync("artifacts/vb-projection-golden.json", JSON.stringify(version, null, 2));
console.log("dumped");
