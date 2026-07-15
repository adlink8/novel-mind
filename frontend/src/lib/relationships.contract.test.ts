/**
 * Phase 09 relationship graph API consumer contracts (09-04).
 * Asserts query param names, no owner_id, and envelope field shape.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const { mockGet, mockPost, mockPut, mockPatch, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn().mockResolvedValue({ data: {} }),
  mockPost: vi.fn().mockResolvedValue({ data: {} }),
  mockPut: vi.fn().mockResolvedValue({ data: {} }),
  mockPatch: vi.fn().mockResolvedValue({ data: {} }),
  mockDelete: vi.fn().mockResolvedValue({ data: {} }),
}));

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => ({
      get: mockGet,
      post: mockPost,
      put: mockPut,
      patch: mockPatch,
      delete: mockDelete,
      defaults: { baseURL: "/api", timeout: 30000 },
      interceptors: {
        request: { use: vi.fn(), eject: vi.fn() },
        response: { use: vi.fn(), eject: vi.fn() },
      },
    })),
  },
}));

import {
  relationshipsApi,
  timelineApi,
  type RelationshipGraphEnvelope,
  type RelationshipEvidenceResponse,
} from "./api";

describe("relationshipsApi contract (09-04)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("serializes source/version/through_chapter/character_id/relation_type/full_book without owner_id", async () => {
    await relationshipsApi.getGraph("42", {
      source: "active",
      version_id: 9,
      through_chapter: 3,
      full_book: false,
      character_id: 11,
      relation_type: "ally",
    });

    expect(mockGet).toHaveBeenCalledTimes(1);
    const [path, config] = mockGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(path).toBe("/relationships/42/graph");
    expect(config.params).toEqual({
      source: "active",
      version_id: 9,
      through_chapter: 3,
      full_book: false,
      character_id: 11,
      relation_type: "ally",
    });
    expect(config.params).not.toHaveProperty("owner_id");
    expect(JSON.stringify(config.params)).not.toContain("owner_id");
  });

  it("serializes running_candidate and omits undefined filter params as-is", async () => {
    await relationshipsApi.getGraph(7, {
      source: "running_candidate",
      full_book: true,
    });
    const [, config] = mockGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(config.params.source).toBe("running_candidate");
    expect(config.params.full_book).toBe(true);
    expect(config.params).not.toHaveProperty("owner_id");
  });

  it("loads evidence with the same spoiler/version scope params", async () => {
    await relationshipsApi.getEvidence("42", 1001, {
      source: "active",
      version_id: 9,
      through_chapter: 3,
      full_book: false,
    });
    expect(mockGet).toHaveBeenCalledWith(
      "/relationships/42/observations/1001/evidence",
      {
        params: {
          source: "active",
          version_id: 9,
          through_chapter: 3,
          full_book: false,
        },
      }
    );
    const [, config] = mockGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(config.params).not.toHaveProperty("owner_id");
  });

  it("types envelope fields for cutoff, provenance, filters, counts, degradation", () => {
    const envelope: RelationshipGraphEnvelope = {
      novel_id: 1,
      version_id: 2,
      source: "active",
      through_chapter: 1,
      full_book: false,
      cutoff_chapter: 1,
      nodes: [
        {
          character_id: 10,
          name: "林墨",
          aliases: ["小墨"],
          first_visible_chapter: 1,
        },
      ],
      edges: [
        {
          observation_id: 55,
          source_character_id: 10,
          target_character_id: 11,
          relation_type: "ally",
          transition: "establish",
          confidence: 0.9,
          valid_from_chapter: 1,
          valid_to_chapter: null,
          provenance: "machine",
          evidence_preview: "他们并肩作战",
          evidence_count: 1,
        },
      ],
      counts: { nodes: 1, edges: 1, relation_types: { ally: 1 } },
      available_relation_types: ["ally"],
      available_character_ids: [10, 11],
      degradation: {
        mode: "normal",
        node_count: 1,
        edge_count: 1,
        hard_node_cap: 500,
        hard_edge_cap: 1500,
        message: null,
      },
      generated_at: null,
    };
    expect(envelope.degradation.mode).toBe("normal");
    expect(envelope.cutoff_chapter).toBe(1);
    expect(envelope.edges[0].provenance).toBe("machine");
    expect(envelope.available_character_ids).toContain(10);

    const evidence: RelationshipEvidenceResponse = {
      observation_id: 55,
      novel_id: 1,
      version_id: 2,
      through_chapter: 1,
      relation_type: "ally",
      source_character_id: 10,
      target_character_id: 11,
      evidence: [
        {
          evidence_id: "ev-1",
          chapter_id: 3,
          source_start: 0,
          source_end: 12,
          content_hash: "a".repeat(64),
          excerpt: "并肩作战",
        },
      ],
      provenance: "manual",
    };
    expect(evidence.evidence[0].chapter_id).toBe(3);
    expect(evidence.provenance).toBe("manual");
  });

  it("preserves timeline API contract unchanged", async () => {
    await timelineApi.getTimeline("7", {
      ordering: "story",
      person: "阿宁",
      causal: true,
      full_book: false,
    });
    expect(mockGet).toHaveBeenCalledWith("/timeline/7", {
      params: {
        ordering: "story",
        person: "阿宁",
        causal: true,
        full_book: false,
      },
    });
  });
});
