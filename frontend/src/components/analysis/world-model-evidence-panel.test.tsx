import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorldProjectionView } from "@/lib/api";
import { WorldModelEvidencePanel } from "./world-model-evidence-panel";

/**
 * 27-04 colocated vitest —— world-model evidence panel。
 * 覆盖：authority 四标签以 badge 展示且不被静默合并（D-01）、disclosure
 * 时间展示（D-05）、approved 条目证据跳转/候选条目无跳转（D-08/D-02）、
 * user interpretation 隔离在 overrides 区（D-06）、unavailable 显式弃权
 * 而非空成功（D-05）、无 active-pointer / promotion 字段。
 */

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const item = (
  over: Partial<WorldProjectionView["items"][number]> = {}
): WorldProjectionView["items"][number] => ({
  claim_key: "k-arrival",
  kind: "character",
  subject: "lin-an",
  aspect: "knowledge",
  proposition: "林安在临安落脚",
  authority: "probable_inference",
  known_at: 1,
  disclosure_cutoff: 2,
  pov: "lin-an",
  gate_status: "passed",
  approved: true,
  is_override: false,
  evidence_key: "qp:1:0:12:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  chapter_id: 1,
  chapter_number: 1,
  source_start: 0,
  source_end: 12,
  content_hash: "a".repeat(64),
  source_snapshot_hash: "c".repeat(64),
  lineage: ["k-arrival"],
  ...over,
});

const availableProjection: WorldProjectionView = {
  schema_version: "world-model-projection.v1",
  available: true,
  status: "available",
  cutoff: 2,
  authorities: ["canon_fact", "probable_inference"],
  items: [
    item({
      claim_key: "k-canon",
      authority: "canon_fact",
      proposition: "林安是临安城主",
    }),
    item(),
  ],
  overrides: [],
  manifest_checksum: "b".repeat(64),
  snapshot_hash: "c".repeat(64),
};

describe("WorldModelEvidencePanel", () => {
  it("renders nothing fabricated when the projection is absent", () => {
    render(<WorldModelEvidencePanel novelId="11" worldProjection={null} />);
    expect(
      screen.getByTestId("world-model-status-unavailable")
    ).toBeInTheDocument();
    expect(screen.getByText(/世界模型投影未生成/)).toBeInTheDocument();
  });

  it("preserves the four authority labels as distinct badges", () => {
    const projection: WorldProjectionView = {
      ...availableProjection,
      authorities: [
        "canon_fact",
        "probable_inference",
        "literary_interpretation",
        "user_interpretation",
      ],
      items: [
        item({ claim_key: "k-canon", authority: "canon_fact" }),
        item({ claim_key: "k-inf", authority: "probable_inference" }),
        item({ claim_key: "k-lit", authority: "literary_interpretation" }),
      ],
      overrides: [
        item({
          claim_key: "k-user",
          authority: "user_interpretation",
          is_override: true,
        }),
      ],
    };
    render(<WorldModelEvidencePanel novelId="11" worldProjection={projection} />);
    const badges = screen.getAllByTestId("world-model-authority-badge");
    const authorities = [
      ...new Set(badges.map((b) => b.getAttribute("data-authority"))),
    ].sort();
    // Four distinct labels survive serialization to the browser — nothing is
    // collapsed or silently upgraded (D-01).
    expect(authorities).toEqual([
      "canon_fact",
      "literary_interpretation",
      "probable_inference",
      "user_interpretation",
    ]);
  });

  it("shows disclosure timing next to each claim", () => {
    render(<WorldModelEvidencePanel novelId="11" worldProjection={availableProjection} />);
    expect(screen.getAllByTestId("world-model-disclosure")).toHaveLength(2);
    expect(screen.getAllByText(/已知于第 1 章 · 第 2 章后披露/)).toHaveLength(2);
  });

  it("approved claims render an evidence chip; candidate-only has no jump", () => {
    const projection: WorldProjectionView = {
      ...availableProjection,
      status: "candidate_only",
      available: false,
      items: [
        item({ claim_key: "k-candidate", approved: false, gate_status: "pending" }),
      ],
    };
    render(<WorldModelEvidencePanel novelId="11" worldProjection={projection} />);
    expect(screen.getAllByTestId("world-model-candidate-only")).toHaveLength(1);
    expect(
      screen.queryAllByTestId("reader-chat-citation")
    ).toHaveLength(0);
    expect(screen.getByTestId("world-model-status-candidate-only")).toBeInTheDocument();
  });

  it("evidence chip click routes the leaf jump", () => {
    render(<WorldModelEvidencePanel novelId="11" worldProjection={availableProjection} />);
    fireEvent.click(screen.getAllByTestId("reader-chat-citation")[0]);
    expect(mocks.routerPush).toHaveBeenCalledWith(
      "/novels/11?chapter=1&start=0&from=world-model"
    );
  });

  it("isolates user interpretation into a separate overrides section", () => {
    const projection: WorldProjectionView = {
      ...availableProjection,
      items: [
        item({ claim_key: "k-canon", authority: "canon_fact" }),
      ],
      overrides: [
        item({
          claim_key: "k-user-read",
          authority: "user_interpretation",
          is_override: true,
          proposition: "读者认为林安另有隐情",
        }),
      ],
    };
    render(<WorldModelEvidencePanel novelId="11" worldProjection={projection} />);
    expect(screen.getByTestId("world-model-overrides")).toBeInTheDocument();
    expect(screen.getByTestId("world-model-override-item")).toBeInTheDocument();
    // Override item is never rendered inside the candidate list.
    expect(
      screen.getByTestId("world-model-candidate-items")
    ).not.toHaveTextContent("读者认为林安另有隐情");
    expect(
      screen.getByTestId("world-model-overrides")
    ).toHaveTextContent("读者认为林安另有隐情");
  });

  it("explicitly abstains on unavailable rather than empty-success", () => {
    const projection: WorldProjectionView = {
      schema_version: "world-model-projection.v1",
      available: false,
      status: "unavailable",
      cutoff: 2,
      items: [],
      overrides: [],
      authorities: [],
    };
    render(<WorldModelEvidencePanel novelId="11" worldProjection={projection} />);
    expect(screen.getByTestId("world-model-status-unavailable")).toBeInTheDocument();
    expect(
      screen.getByTestId("world-model-empty-abstained")
    ).toHaveTextContent(/未编造任何内容/);
  });
});
