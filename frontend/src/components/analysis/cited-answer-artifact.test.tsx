import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ARTIFACT_RENDERERS,
  ArtifactPreview,
  CitedAnswerArtifactView,
  ExternalEvidenceView,
  resolveArtifactRenderer,
} from "./cited-answer-artifact";
import type { ArtifactView } from "@/lib/api";

/**
 * 25.3-05 colocated vitest——pi-web-ui 渲染器注册表（模式借用，零 import）。
 * - vi.hoisted：router.push spy（CitedAnswerArtifactView 内部 useRouter）。
 * - 覆盖：cited_answer 渲染块+每证据一个芯片；点击芯片 router.push 带
 *   chapter/start 查询；external_evidence 渲染标签且内部无 reader-citation；
 *   unknown 类型落 fallback；注册表恰好两个已知类型（漂移守卫）。
 */

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

const citedAnswerArtifact: ArtifactView = {
  id: 5,
  type: "cited_answer",
  schema_version: "cited-answer.v1",
  status: "candidate",
  content: {
    answer: {
      answer_blocks: [
        {
          text: "阿宁在竹林中遇见了林墨。",
          citations: [
            {
              chapter_id: 23,
              source_start: 10,
              source_end: 14,
              evidence_key: "chapter:23:10:14",
              block_id: "b1",
              context_evidence_ref_id: 7,
            },
          ],
        },
        {
          text: "月光洒在青石上。",
          citations: [
            {
              chapter_id: 24,
              source_start: 5,
              source_end: 9,
              evidence_key: "chapter:24:5:9",
              block_id: "b2",
              context_evidence_ref_id: 8,
            },
          ],
        },
      ],
    },
  },
};

const externalEvidenceArtifact = {
  id: 6,
  type: "external_evidence",
  schema_version: "1",
  status: "candidate",
  content: {
    sources: [
      {
        server: "external-research",
        tool: "web_search",
        uri: "https://example.com/source",
        title: "外部资料站",
        retrieved_from: "mcp",
      },
    ],
    retrieval_time: "2026-08-01T00:00:00Z",
    claims: [{ text: "某外部主张。", source_index: 0 }],
    confidence: "medium",
    prohibited_from_canon: true,
    release_status: "external",
  },
} as ArtifactView;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CitedAnswerArtifactView", () => {
  it("renders answer blocks with one citation chip per evidence ref", () => {
    render(<CitedAnswerArtifactView artifact={citedAnswerArtifact} novelId="11" />);
    expect(screen.getByTestId("analysis-artifact-cited-answer")).toBeInTheDocument();
    expect(screen.getByText("阿宁在竹林中遇见了林墨。")).toBeInTheDocument();
    expect(screen.getByText("月光洒在青石上。")).toBeInTheDocument();
    // 每个证据引用一个芯片：2 个 answer_blocks × 1 citation = 2 chips
    expect(screen.getAllByTestId("reader-chat-citation")).toHaveLength(2);
  });

  it("chip click routes the citation jump with chapter/start query", () => {
    render(<CitedAnswerArtifactView artifact={citedAnswerArtifact} novelId="11" />);
    fireEvent.click(screen.getAllByTestId("reader-chat-citation")[0]);
    expect(mocks.routerPush).toHaveBeenCalledWith(
      "/novels/11?chapter=23&start=10&from=timeline"
    );
  });
});

describe("ExternalEvidenceView", () => {
  it("renders the canon-prohibited label and zero reader citations inside it", () => {
    render(<ExternalEvidenceView artifact={externalEvidenceArtifact} novelId="11" />);
    const root = screen.getByTestId("analysis-artifact-external-evidence");
    expect(root).toBeInTheDocument();
    expect(
      screen.getByText("External evidence — prohibited from canon")
    ).toBeInTheDocument();
    // 外部证据不得出现可跳转正文的 reader-citation 元素（D-08/D-09）
    expect(
      root.querySelector('[data-testid="reader-chat-citation"]')
    ).toBeNull();
  });

  it("renders sources, claims and confidence from the D-09 content", () => {
    render(<ExternalEvidenceView artifact={externalEvidenceArtifact} novelId="11" />);
    expect(screen.getByText("外部资料站")).toBeInTheDocument();
    expect(screen.getByText("某外部主张。")).toBeInTheDocument();
    expect(screen.getByText(/confidence medium/)).toBeInTheDocument();
  });
});

describe("resolveArtifactRenderer / ArtifactPreview", () => {
  it("unknown artifact type renders the explicit fallback, never crashes", () => {
    const Renderer = resolveArtifactRenderer("mystery_type");
    const { container } = render(
      <Renderer
        artifact={
          {
            id: 9,
            type: "mystery_type",
            schema_version: "v1",
            status: "candidate",
          } as ArtifactView
        }
        novelId="11"
      />
    );
    expect(screen.getByTestId("analysis-artifact-unknown")).toBeInTheDocument();
    expect(container.querySelector('[data-testid="reader-chat-citation"]')).toBeNull();
  });

  it("registry contains exactly the two known artifact types (drift guard)", () => {
    expect(Object.keys(ARTIFACT_RENDERERS).sort()).toEqual([
      "cited_answer",
      "external_evidence",
    ]);
  });

  it("ArtifactPreview resolves by type through the registry", () => {
    render(<ArtifactPreview artifact={citedAnswerArtifact} novelId="11" />);
    expect(screen.getByTestId("analysis-artifact-cited-answer")).toBeInTheDocument();
    expect(screen.getAllByTestId("reader-chat-citation")).toHaveLength(2);
  });
});
