/**
 * Phase 11-04 clue API consumer contracts.
 * Asserts paths, params and action payloads against /api/clues.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

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
  clueApi,
  sortVisibleClues,
  type ClueEnvelope,
  type ClueVersionView,
  type VisibleClue,
} from "./clue-api";

describe("clueApi contract (11-04)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists clues with status/person/full_book without client-derived counts or owner_id", async () => {
    await clueApi.getClues("42", {
      full_book: false,
      character_id: 11,
      status: "active",
    });

    expect(mockGet).toHaveBeenCalledTimes(1);
    const [path, config] = mockGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(path).toBe("/clues/42");
    expect(config.params).toEqual({
      full_book: false,
      character_id: 11,
      status: "active",
    });
    expect(config.params).not.toHaveProperty("owner_id");
    expect(JSON.stringify(config.params)).not.toContain("hidden");
  });

  it("keeps active/running_candidate sources as separate envelope slots", () => {
    const envelope: ClueEnvelope = {
      active: {
        novel_id: 1,
        version_id: 7,
        source: "active",
        through_chapter: 2,
        full_book: false,
        cutoff_chapter: 2,
        clues: [],
        counts: { clues: 0, by_state: {} },
        available_states: [],
        available_character_ids: [],
      },
      running_candidate: {
        novel_id: 1,
        version_id: 8,
        source: "running_candidate",
        through_chapter: 9,
        full_book: true,
        cutoff_chapter: 9,
        clues: [],
        counts: { clues: 0, by_state: {}, status: "running" },
        available_states: ["candidate"],
        available_character_ids: [3],
      },
    };
    expect(envelope.active?.version_id).toBe(7);
    expect(envelope.running_candidate?.version_id).toBe(8);
    expect(envelope.active?.source).not.toBe(envelope.running_candidate?.source);
  });

  it("loads version and detail with full_book only (no clue preference endpoint)", async () => {
    await clueApi.getVersion(7, 12, { full_book: true, status: "reinforced" });
    expect(mockGet).toHaveBeenCalledWith("/clues/7/versions/12", {
      params: {
        full_book: true,
        character_id: undefined,
        status: "reinforced",
      },
    });

    await clueApi.getDetail(7, 12, "clue-a", { full_book: false });
    expect(mockGet).toHaveBeenCalledWith(
      "/clues/7/versions/12/clues/clue-a",
      { params: { full_book: false } }
    );

    // Contract: no full-book mutation lives on clueApi.
    expect(clueApi).not.toHaveProperty("setFullBookPreference");
    expect(Object.keys(clueApi).every((k) => !k.toLowerCase().includes("preference"))).toBe(
      true
    );
  });

  it("sends distinct typed action payloads for confirm/reject/annotate/adjustLink", async () => {
    await clueApi.action("9", "c-1", {
      action: "confirm",
      reason: "证据充分",
    });
    expect(mockPost).toHaveBeenLastCalledWith(
      "/clues/9/clues/c-1/actions",
      { action: "confirm", reason: "证据充分" }
    );

    await clueApi.action("9", "c-1", {
      action: "reject",
      reason: "母题误判",
    });
    expect(mockPost).toHaveBeenLastCalledWith(
      "/clues/9/clues/c-1/actions",
      { action: "reject", reason: "母题误判" }
    );

    await clueApi.action("9", "c-1", {
      action: "annotate",
      reason: "备注",
      note: "跨章呼应",
    });
    expect(mockPost).toHaveBeenLastCalledWith(
      "/clues/9/clues/c-1/actions",
      { action: "annotate", reason: "备注", note: "跨章呼应" }
    );

    await clueApi.action("9", "c-1", {
      action: "adjust_link",
      reason: "修正人物关联",
      link: {
        target_kind: "character",
        character_id: 42,
        validation_status: "valid",
      },
    });
    expect(mockPost).toHaveBeenLastCalledWith(
      "/clues/9/clues/c-1/actions",
      {
        action: "adjust_link",
        reason: "修正人物关联",
        link: {
          target_kind: "character",
          character_id: 42,
          validation_status: "valid",
        },
      }
    );
  });

  it("drives durable lifecycle and version diff without merging sources", async () => {
    await clueApi.startOrResume("7");
    expect(mockPost).toHaveBeenCalledWith(
      "/clues/7/start-or-resume",
      null,
      { timeout: 300_000 }
    );
    await clueApi.status("7");
    expect(mockGet).toHaveBeenCalledWith("/clues/7/status");
    await clueApi.cancel("7");
    expect(mockPost).toHaveBeenCalledWith("/clues/7/cancel");
    await clueApi.resume("7");
    expect(mockPost).toHaveBeenCalledWith("/clues/7/resume", null, {
      timeout: 300_000,
    });
    await clueApi.reanalyze("7");
    expect(mockPost).toHaveBeenCalledWith("/clues/7/reanalyze", null, {
      timeout: 300_000,
    });
    await clueApi.compare("7", 3, 5);
    expect(mockGet).toHaveBeenCalledWith("/clues/7/compare", {
      params: { from_version_id: 3, to_version_id: 5 },
    });
  });

  it("types version view evidence/link/version provenance fields for the panel", () => {
    const view: ClueVersionView = {
      novel_id: 1,
      version_id: 2,
      source: "active",
      through_chapter: 1,
      full_book: false,
      cutoff_chapter: 1,
      clues: [
        {
          logical_clue_id: "c1",
          title: "雾中铃铛",
          derived_state: "reinforced",
          narrative_chapter_number: 1,
          source_start: 12,
          confidence: 0.8,
          evidence_count: 2,
          link_count: 1,
          provenance: { state: "machine", note: "manual" },
        },
      ],
      counts: {
        clues: 1,
        by_state: { reinforced: 1 },
        status: "completed",
      },
      available_states: ["reinforced"],
      available_character_ids: [10],
    };
    expect(view.clues[0].provenance.note).toBe("manual");
    expect(view.counts.by_state.reinforced).toBe(1);
    expect(view.available_states).toEqual(["reinforced"]);
  });

  it("sorts band/list by chapter then source_start then id", () => {
    const clues: VisibleClue[] = [
      {
        logical_clue_id: "z",
        title: "后",
        derived_state: "active",
        narrative_chapter_number: 2,
        source_start: 10,
        confidence: 0.5,
        evidence_count: 1,
        link_count: 0,
        provenance: {},
      },
      {
        logical_clue_id: "a",
        title: "前",
        derived_state: "active",
        narrative_chapter_number: 1,
        source_start: 90,
        confidence: 0.5,
        evidence_count: 1,
        link_count: 0,
        provenance: {},
      },
      {
        logical_clue_id: "b",
        title: "中",
        derived_state: "active",
        narrative_chapter_number: 2,
        source_start: 5,
        confidence: 0.5,
        evidence_count: 1,
        link_count: 0,
        provenance: {},
      },
    ];
    expect(sortVisibleClues(clues).map((c) => c.logical_clue_id)).toEqual([
      "a",
      "b",
      "z",
    ]);
  });
});
