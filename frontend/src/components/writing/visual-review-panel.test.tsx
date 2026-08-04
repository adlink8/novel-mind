import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  DerivativeAssetState,
  DerivativeVisualAssetView,
} from "@/lib/derivative-visual-api";
import {
  ACTION_LABEL_TEXT,
  LEGAL_DERIVATIVE_REVIEW_ACTIONS,
  REVIEW_STATE_LABEL_TEXT,
  VisualReviewPanel,
  shortVisualHash,
} from "./visual-review-panel";

/**
 * 38-04 colocated vitest —— derivative visual review panel.
 *
 * 覆盖 D-38-03 / REQ-FORK-04 的前端约束：
 * - 候选队列与详情全部来自 owner-scoped 服务端 envelope；
 * - source refs / identity+style 评分 / divergence manifest / namespace /
 *   审查事件链都从 envelope 渲染；
 * - approve/reject 必须带显式理由，action 只提交一次、from_review_state 从
 *   候选当前状态回传，review truth 由服务端返回（前端不保存）；
 * - blocked / superseded 状态锁定，不出现 auto-approve；
 * - accept/reject/compare/reload 后从 API 重新拉取，显示状态与服务端一致。
 */

const mocks = vi.hoisted(() => ({
  listReviewCandidates: vi.fn(),
  getReviewCandidate: vi.fn(),
  reviewCandidate: vi.fn(),
}));

vi.mock("@/lib/derivative-visual-api", () => ({
  derivativeVisualApi: {
    listReviewCandidates: mocks.listReviewCandidates,
    getReviewCandidate: mocks.getReviewCandidate,
    reviewCandidate: mocks.reviewCandidate,
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const H = (n: number) => String(n).repeat(64);

const asset = (over: Partial<DerivativeVisualAssetView> = {}) =>
  ({
    id: 1,
    owner_id: 11,
    novel_id: 22,
    project_id: 33,
    fork_id: 44,
    asset_id: "dv-abc",
    asset_key: "cand-1",
    content_hash: H(1),
    mime_type: "image/png",
    size_bytes: 16,
    namespace: "fanfiction_visual",
    scene_spec_hash: H(2),
    chapter_number: 1,
    visual_version: {
      version_id: 9,
      version_key: "dv-visual-1",
      version_hash: H(3),
    },
    source_snapshot: {
      source_snapshot_id: "snap-1",
      source_snapshot_hash: H(4),
      source_manifest_hash: H(5),
      cutoff_chapter: 8,
    },
    approval: "candidate",
    review: {
      review_state: "candidate",
      consistency_verdict: "pass",
      consistency_report: {
        schema_version: "derivative-visual-asset.v1",
        evaluator_id: "derivative-visual-consistency.cross_chapter.v1",
        evaluator_version: "1.0.0",
        chapters: [
          {
            chapter_number: 1,
            identity_score: 1.0,
            style_score: 1.0,
            identity_consistent: true,
            style_consistent: true,
          },
        ],
        reasons: [],
        verdict: "pass",
        details: {},
      },
      reasons: [],
      review_events: [],
    },
    source_refs: [
      {
        asset_key: "dv-arya",
        asset_id: "dv-obj-1",
        source_asset_id: "obj-1",
        source_bytes_hash: H(6),
      },
    ],
    identity_lineage: [
      {
        stable_id: "char-arya",
        entity_key: "char-arya",
        entity_type: "character",
        source_entity_hash: H(7),
      },
    ],
    generator_lineage: { provider: "mock", provider_model: "mock-1" },
    divergence_manifest_hash: H(8),
    ...over,
  }) as DerivativeVisualAssetView;

function resolveQueue(items: DerivativeVisualAssetView[]) {
  mocks.listReviewCandidates.mockResolvedValue({ data: { items, total: items.length } });
}

function resolveDetail(view: DerivativeVisualAssetView) {
  mocks.getReviewCandidate.mockResolvedValue({ data: view });
}

describe("VisualReviewPanel", () => {
  it("shows a loading state before the queue resolves", () => {
    let resolveFn: (v: { data: { items: DerivativeVisualAssetView[]; total: number } }) => void = () => undefined;
    mocks.listReviewCandidates.mockReturnValue(
      new Promise((res) => {
        resolveFn = res;
      })
    );
    render(<VisualReviewPanel novelId="22" />);
    expect(screen.getByTestId("derivative-review-loading")).toBeInTheDocument();
    resolveFn({ data: { items: [], total: 0 } });
  });

  it("renders the error state when the queue fails", async () => {
    mocks.listReviewCandidates.mockRejectedValue(new Error("network"));
    render(<VisualReviewPanel novelId="22" />);
    const error = await screen.findByTestId("derivative-review-error");
    expect(error).toHaveTextContent("候选列表加载失败");
  });

  it("renders an explicit empty state instead of empty-success", async () => {
    resolveQueue([]);
    render(<VisualReviewPanel novelId="22" />);
    expect(await screen.findByTestId("derivative-review-empty")).toHaveTextContent(
      "当前小说没有 derivative 视觉候选"
    );
  });

  it("renders source refs, identity, divergence, namespace and verdict from the envelope", async () => {
    resolveQueue([asset()]);
    resolveDetail(asset());
    render(<VisualReviewPanel novelId="22" />);
    const detail = await screen.findByTestId("derivative-review-detail");

    expect(detail).toHaveAttribute("data-review-state", "candidate");
    expect(screen.getByTestId("derivative-review-namespace")).toHaveTextContent(
      "fanfiction_visual"
    );
    expect(screen.getByTestId("derivative-review-source-ref")).toHaveTextContent(
      "dv-arya"
    );
    expect(screen.getByTestId("derivative-review-source-ref")).toHaveTextContent(
      shortVisualHash(H(6))
    );
    expect(screen.getByTestId("derivative-review-identity")).toHaveTextContent(
      "char-arya"
    );
    expect(screen.getByTestId("derivative-review-divergence")).toHaveTextContent(
      shortVisualHash(H(8))
    );
    expect(screen.getByTestId("derivative-review-verdict")).toHaveTextContent("pass");
  });

  it("shows the append-only review event chain from the envelope", async () => {
    const view = asset({
      review: {
        review_state: "approved",
        consistency_verdict: "pass",
        consistency_report: null,
        reasons: [],
        review_events: [
          {
            action: "approve",
            actor_source: "human",
            actor: "owner",
            reason: "人工审查：批准 — ok",
            event_key: "ev-1",
            from_review_state: "candidate",
            to_review_state: "approved",
          },
        ],
      },
      approval: "approved",
    });
    resolveQueue([view]);
    resolveDetail(view);
    render(<VisualReviewPanel novelId="22" />);
    await screen.findByTestId("derivative-review-detail");

    const event = screen.getByTestId("derivative-review-event");
    expect(event).toHaveAttribute("data-action", "approve");
    expect(event).toHaveTextContent("candidate → approved");
    expect(event).toHaveTextContent("人工审查：批准 — ok");
  });

  it("requires an explicit reason before approve/reject can be submitted", async () => {
    resolveQueue([asset()]);
    resolveDetail(asset());
    render(<VisualReviewPanel novelId="22" />);
    await screen.findByTestId("derivative-review-detail");

    const approve = screen.getByTestId(
      "derivative-review-action-approve"
    ) as HTMLButtonElement;
    // No auto-approve: without a reason the action stays disabled.
    expect(approve.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("derivative-review-reason"), {
      target: { value: "与分支场景一致" },
    });
    expect(approve.disabled).toBe(false);
  });

  it("submits one explicit approval carrying from_review_state and re-fetches after", async () => {
    const candidate = asset();
    resolveQueue([candidate]);
    let calls = 0;
    mocks.getReviewCandidate.mockImplementation(async () => {
      calls += 1;
      return {
        data:
          calls > 1
            ? asset({
                review: {
                  review_state: "approved",
                  consistency_verdict: "pass",
                  consistency_report: null,
                  reasons: [],
                  review_events: [],
                },
                approval: "approved",
              })
            : candidate,
      };
    });
    mocks.reviewCandidate.mockResolvedValue({
      data: {
        asset: asset({
          review: {
            review_state: "approved",
            consistency_verdict: "pass",
            consistency_report: null,
            reasons: [],
            review_events: [],
          },
          approval: "approved",
        }),
      },
    });
    render(<VisualReviewPanel novelId="22" />);
    const detail = await screen.findByTestId("derivative-review-detail");
    expect(detail).toHaveAttribute("data-review-state", "candidate");

    fireEvent.change(screen.getByTestId("derivative-review-reason"), {
      target: { value: "三章一致" },
    });
    fireEvent.click(screen.getByTestId("derivative-review-action-approve"));

    await waitFor(() => expect(mocks.reviewCandidate).toHaveBeenCalledTimes(1));
    const [novelId, candidateId, body] = mocks.reviewCandidate.mock.calls[0];
    expect(novelId).toBe("22");
    expect(candidateId).toBe(candidate.id);
    expect(body.action).toBe("approve");
    expect(body.actor_source).toBe("human");
    expect(body.actor).toBe("owner");
    expect(body.reason).toContain("三章一致");
    expect(body.from_review_state).toBe("candidate");
    expect(body.event_key).toMatch(/^dv-1-approve-/);

    // Review truth is not saved client-side; the server envelope re-drives it.
    await waitFor(() =>
      expect(screen.getByTestId("derivative-review-detail")).toHaveAttribute(
        "data-review-state",
        "approved"
      )
    );
  });

  it("locks a blocked candidate: no action can be submitted and it is never publishable", async () => {
    const blocked = asset({
      review: {
        review_state: "blocked",
        consistency_verdict: "fail",
        consistency_report: {
          schema_version: "derivative-visual-asset.v1",
          evaluator_id: "derivative-visual-consistency.cross_chapter.v1",
          evaluator_version: "1.0.0",
          chapters: [],
          reasons: ["identity_drift:chapter2"],
          verdict: "fail",
          details: {},
        },
        reasons: ["identity_drift:chapter2"],
        review_events: [],
      },
      approval: "blocked",
    });
    resolveQueue([blocked]);
    resolveDetail(blocked);
    render(<VisualReviewPanel novelId="22" />);
    const detail = await screen.findByTestId("derivative-review-detail");
    expect(detail).toHaveAttribute("data-review-state", "blocked");
    expect(screen.getByTestId("derivative-review-locked")).toHaveTextContent(
      "不可发布"
    );
    expect(
      screen.queryByTestId("derivative-review-actions")
    ).not.toBeInTheDocument();
    expect(mocks.reviewCandidate).not.toHaveBeenCalled();
  });

  it("locks a superseded candidate and maps the state/action vocabularies", async () => {
    const superseded = asset({
      review: {
        review_state: "superseded",
        consistency_verdict: "pass",
        consistency_report: null,
        reasons: [],
        review_events: [],
      },
      approval: "superseded",
    });
    resolveQueue([superseded]);
    resolveDetail(superseded);
    render(<VisualReviewPanel novelId="22" />);
    await screen.findByTestId("derivative-review-detail");
    expect(screen.getByTestId("derivative-review-locked")).toBeInTheDocument();
    expect(
      screen.queryByTestId("derivative-review-actions")
    ).not.toBeInTheDocument();

    // Display vocabulary mirrors the backend closed enums.
    expect(REVIEW_STATE_LABEL_TEXT.blocked).toContain("阻断");
    expect(ACTION_LABEL_TEXT.approve).toBe("批准");
    expect(LEGAL_DERIVATIVE_REVIEW_ACTIONS.blocked).toEqual([]);
    expect(LEGAL_DERIVATIVE_REVIEW_ACTIONS.superseded).toEqual([]);
    expect(LEGAL_DERIVATIVE_REVIEW_ACTIONS.needs_review).toEqual([
      "approve",
      "reject",
      "supersede",
    ]);
  });

  it("opens the chapter comparison table with per-chapter identity/style scores", async () => {
    const view = asset({
      review: {
        review_state: "candidate",
        consistency_verdict: "concern",
        consistency_report: {
          schema_version: "derivative-visual-asset.v1",
          evaluator_id: "derivative-visual-consistency.cross_chapter.v1",
          evaluator_version: "1.0.0",
          chapters: [
            {
              chapter_number: 1,
              identity_score: 1.0,
              style_score: 1.0,
              identity_consistent: true,
              style_consistent: true,
            },
            {
              chapter_number: 2,
              identity_score: 1.0,
              style_score: 0.0,
              identity_consistent: true,
              style_consistent: false,
            },
          ],
          reasons: ["style_divergence_declared:chapter2"],
          verdict: "concern",
          details: {},
        },
        reasons: ["style_divergence_declared:chapter2"],
        review_events: [],
      },
    });
    resolveQueue([view]);
    resolveDetail(view);
    render(<VisualReviewPanel novelId="22" />);
    await screen.findByTestId("derivative-review-detail");

    fireEvent.click(screen.getByTestId("derivative-review-compare-toggle"));
    const chapters = screen.getAllByTestId("derivative-review-chapter");
    expect(chapters).toHaveLength(2);
    expect(chapters[0]).toHaveTextContent("第 1 章");
    expect(chapters[1]).toHaveTextContent("1.0");
    expect(chapters[1]).toHaveTextContent("0.0");
    expect(screen.getByTestId("derivative-review-reasons")).toHaveTextContent(
      "style_divergence_declared:chapter2"
    );
  });

  it("keeps state consistent after reload by re-fetching list and detail", async () => {
    const candidate = asset();
    resolveQueue([candidate]);
    resolveDetail(candidate);
    render(<VisualReviewPanel novelId="22" />);
    await screen.findByTestId("derivative-review-detail");

    mocks.listReviewCandidates.mockClear();
    mocks.getReviewCandidate.mockClear();
    resolveQueue([candidate]);
    resolveDetail(candidate);
    fireEvent.click(screen.getByTestId("derivative-review-reload"));

    await waitFor(() => {
      expect(mocks.listReviewCandidates).toHaveBeenCalled();
      expect(mocks.getReviewCandidate).toHaveBeenCalledWith("22", candidate.id);
    });
  });
});

describe("shortVisualHash", () => {
  it("shortens sha256 hashes for display and passes short values through", () => {
    expect(shortVisualHash(H(9))).toMatch(/^.{8}….{4}$/);
    expect(shortVisualHash("abc")).toBe("abc");
    expect(shortVisualHash(null)).toBe("");
  });
});
