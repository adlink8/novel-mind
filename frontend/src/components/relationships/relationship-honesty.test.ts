import { describe, expect, it } from "vitest";

import type { RelationshipGraphEdge } from "@/lib/api";
import {
  edgeCompanionMeta,
  edgeHonestyLabel,
  isProvisionalEdge,
  nonEstablishTransitionLabel,
} from "./relationship-honesty";

function edge(
  overrides: Partial<RelationshipGraphEdge> = {}
): RelationshipGraphEdge {
  return {
    observation_id: 1,
    source_character_id: 1,
    target_character_id: 2,
    relation_type: "ally",
    transition: "establish",
    confidence: 0.9,
    valid_from_chapter: 1,
    valid_to_chapter: null,
    provenance: "machine",
    evidence_preview: null,
    evidence_count: 1,
    edge_kind: "accepted_observation",
    ...overrides,
  };
}

describe("relationship honesty helpers", () => {
  it("hides badge for default establish transition", () => {
    expect(nonEstablishTransitionLabel("establish")).toBeNull();
    expect(nonEstablishTransitionLabel(undefined)).toBeNull();
  });

  it("surfaces change and end transitions", () => {
    expect(nonEstablishTransitionLabel("change")).toBe("变化");
    expect(nonEstablishTransitionLabel("end")).toBe("结束");
  });

  it("keeps Phase 19 provisional cooccur honesty", () => {
    const provisional = edge({
      relation_type: "cooccur",
      edge_kind: "provisional_cooccurrence",
      suggested_type: "ally",
      transition: "establish",
    });
    expect(isProvisionalEdge(provisional)).toBe(true);
    expect(edgeHonestyLabel(provisional)).toBe("共现");
    expect(edgeCompanionMeta(provisional)).toBe("临时共现 · 提示同盟");
  });

  it("appends transition suffix only for non-establish accepted edges", () => {
    const established = edge({ transition: "establish" });
    expect(edgeHonestyLabel(established)).toBe("同盟");
    expect(edgeCompanionMeta(established)).toBe("同盟");

    const changed = edge({ transition: "change", relation_type: "enemy" });
    expect(edgeHonestyLabel(changed)).toBe("敌对 · 变化");
    expect(edgeCompanionMeta(changed)).toBe("敌对 · 变化");
  });

  it("does not treat accepted ally as provisional without edge_kind", () => {
    const accepted = edge({
      relation_type: "ally",
      edge_kind: "accepted_observation",
    });
    expect(isProvisionalEdge(accepted)).toBe(false);
  });
});
