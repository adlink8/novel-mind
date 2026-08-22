import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  VisualBibleVersionView,
  VisualReferenceAssetView,
} from "@/lib/visual-bible-api";
import {
  pickLatestVisualBibleVersion,
  shortVisualHash,
} from "@/lib/visual-bible-api";
import { VisualBibleEntitySheet } from "./entity-sheet";
import { ReferenceAssetStatus } from "./reference-asset-status";
import {
  REVIEW_ACTION_LABEL_TEXT,
  VisualReviewActions,
} from "./review-actions";

/**
 * 30-03 colocated vitest —— Visual Bible 工作区。
 * 覆盖 D-30-01..D-30-04 的前端约束：
 * - 服务端 envelope 渲染，错误/partial/empty 状态可见；
 * - authority 四标签 badge 不折叠（canon vs interpretation 区分）；
 * - canon claim 的证据面板显示 chapter/range/hash/cutoff，leaf jump 生效；
 * - 无证据 canon claim 显示 unresolved 且不显示为 approved；
 * - candidate-only banner 与 needs_relink/rejected 等 review 状态可见；
 * - reference asset 的 rights/provenance 状态与未批准门禁；
 * - review 只提交显式 action，review truth 由服务端返回，前端不保存。
 */

const mocks = vi.hoisted(() => ({
  review: vi.fn(),
  routerPush: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

vi.mock("@/lib/visual-bible-api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/visual-bible-api")>();
  return {
    ...mod,
    visualBibleApi: {
      ...mod.visualBibleApi,
      review: mocks.review,
    },
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const H = (n: number) => String(n).repeat(64);

const evidenceRef = (over: Record<string, unknown> = {}) => ({
  evidence_key: "ev-1",
  source_snapshot_id: "snap-1",
  source_snapshot_hash: H(2),
  chapter_id: 101,
  chapter_number: 1,
  source_start: 6,
  source_end: 10,
  content_hash: H(3),
  excerpt: "走进竹林",
  cutoff_chapter: 2,
  ...over,
});

const claim = (over: Record<string, unknown> = {}) => ({
  claim_key: "cl-1",
  entity_stable_id: "lin-an",
  authority: "canon_fact",
  description: "林安是临安城主",
  author: null,
  rationale: null,
  cutoff_chapter: 2,
  claim_hash: H(4),
  evidence_refs: [evidenceRef()],
  ...over,
});

const entity = (over: Record<string, unknown> = {}) => ({
  stable_id: "lin-an",
  entity_key: "林安",
  entity_type: "character",
  description: "银发青年，常年披深青色斗篷。",
  authority: "canon_fact",
  disclosure_cutoff: 2,
  claims: [claim()],
  ...over,
});

const asset = (over: Record<string, unknown> = {}) =>
  ({
    asset_key: "ref-lin-an",
    asset_id: "asset-1",
    mime_type: "image/png",
    bytes_hash: H(5),
    rights_status: "unreviewed",
    approved: false,
    ...over,
  }) as VisualReferenceAssetView;

const version = (over: Record<string, unknown> = {}) =>
  ({
    id: 1,
    owner_id: 1,
    novel_id: 11,
    version_key: "vb-v1",
    revision_number: 1,
    parent_version_id: null,
    source_snapshot_id: "snap-1",
    source_snapshot_hash: H(2),
    cutoff_chapter: 2,
    schema_version: "visual-bible.v1",
    schema_hash: H(6),
    policy_hash: H(7),
    manifest_hash: H(8),
    review_state: "candidate",
    style_profile: null,
    constraints: null,
    entities: [entity()],
    reference_assets: [asset()],
    review_events: [],
    ...over,
  }) as VisualBibleVersionView;

const resolveLoader = (v: VisualBibleVersionView) => vi.fn(async () => v);

describe("VisualBibleEntitySheet (envelope workspace)", () => {
  it("shows a loading state before the server envelope resolves", async () => {
    let resolveFn: (v: VisualBibleVersionView) => void = () => undefined;
    const loader = vi.fn(
      () =>
        new Promise<VisualBibleVersionView>((res) => {
          resolveFn = res;
        })
    );
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    expect(screen.getByTestId("visual-bible-loading")).toBeInTheDocument();
    resolveFn(version());
    await screen.findByTestId("visual-bible-entity-sheet");
  });

  it("renders the error state when the loader fails (no silent empty success)", async () => {
    const loader = vi.fn(async () => {
      throw new Error("gate failed: offsets out of range");
    });
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    const error = await screen.findByTestId("visual-bible-error");
    expect(error).toHaveTextContent("gate failed: offsets out of range");
  });

  it("renders an explicit empty state instead of empty-success", async () => {
    const loader = resolveLoader(
      version({ entities: [], reference_assets: [] })
    );
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    expect(await screen.findByTestId("visual-bible-empty")).toHaveTextContent(
      "显示为空但不视为成功"
    );
  });

  it("renders entities from the server envelope with distinct authority labels", async () => {
    const loader = resolveLoader(
      version({
        entities: [
          entity({ claims: [claim({ claim_key: "cl-canon" })] }),
          entity({
            stable_id: "lin-an-infer",
            entity_key: "林安（推断）",
            claims: [
              claim({
                claim_key: "cl-inf",
                authority: "probable_inference",
                author: "系统",
                rationale: "依据第二章的暗示",
              }),
            ],
          }),
        ],
      })
    );
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    await screen.findByTestId("visual-bible-entity-sheet");

    expect(screen.getAllByTestId("visual-bible-entity")).toHaveLength(2);
    const authorities = [
      ...new Set(
        screen
          .getAllByTestId("visual-bible-authority-badge")
          .map((b) => b.getAttribute("data-authority"))
      ),
    ].sort();
    // canon and interpretation never collapse into one label (D-30-02).
    expect(authorities).toEqual(["canon_fact", "probable_inference"]);
  });

  it("renders evidence with chapter/range/hash/cutoff and routes the leaf jump", async () => {
    const loader = resolveLoader(version());
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    await screen.findByTestId("visual-bible-entity-sheet");

    const panel = screen.getByTestId("visual-bible-evidence-panel");
    expect(panel).toHaveTextContent("第 1 章 · 范围 6–10");
    expect(panel).toHaveTextContent("截止第 2 章");
    expect(screen.getByTestId("visual-bible-evidence-hash")).toHaveTextContent(
      shortVisualHash(H(3))
    );

    fireEvent.click(screen.getAllByTestId("visual-bible-evidence-jump")[0]);
    expect(mocks.routerPush).toHaveBeenCalledWith(
      "/novels/11?chapter=101&start=6&from=visual-bible"
    );
  });

  it("surfaces a canon_fact without evidence as unresolved, never approved", async () => {
    const loader = resolveLoader(
      version({
        entities: [
          entity({
            claims: [
              claim({ claim_key: "cl-naked", evidence_refs: [] }),
            ],
          }),
        ],
      })
    );
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    await screen.findByTestId("visual-bible-entity-sheet");

    expect(
      screen.getByTestId("visual-bible-claim-unresolved")
    ).toHaveTextContent("未通过验证，不可审批");
    // No jump chip and no approved marker for the unresolved claim.
    expect(
      screen.queryAllByTestId("visual-bible-evidence-jump")
    ).toHaveLength(0);
    expect(
      screen.queryByTestId("visual-bible-asset-approved")
    ).not.toBeInTheDocument();
  });

  it("keeps interpretation claims distinct with author/rationale, no jump", async () => {
    const loader = resolveLoader(
      version({
        entities: [
          entity({
            claims: [
              claim({
                claim_key: "cl-user",
                authority: "user_interpretation",
                author: "读者·小雨",
                rationale: "读者认为林安另有隐情",
                evidence_refs: [],
              }),
            ],
          }),
        ],
      })
    );
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    await screen.findByTestId("visual-bible-entity-sheet");

    const claimNode = screen.getByTestId("visual-bible-claim");
    expect(claimNode).toHaveAttribute("data-authority", "user_interpretation");
    expect(screen.getByTestId("visual-bible-claim-rationale")).toHaveTextContent(
      "作者：读者·小雨"
    );
    expect(screen.getByTestId("visual-bible-claim-rationale")).toHaveTextContent(
      "读者认为林安另有隐情"
    );
    expect(
      screen.queryAllByTestId("visual-bible-evidence-jump")
    ).toHaveLength(0);
  });

  it("shows the candidate-only banner and review state for an unapproved revision", async () => {
    const loader = resolveLoader(version({ review_state: "needs_relink" }));
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    const sheet = await screen.findByTestId("visual-bible-entity-sheet");

    expect(sheet).toHaveAttribute("data-review-state", "needs_relink");
    expect(
      screen.getByTestId("visual-bible-review-state-badge")
    ).toHaveAttribute("data-state", "needs_relink");
    expect(screen.getByTestId("visual-bible-candidate-only")).toBeInTheDocument();
    // needs_relink is not approved — never shown as canon.
    expect(
      screen.queryByTestId("visual-bible-asset-approved")
    ).not.toBeInTheDocument();
  });

  it("hides the candidate-only banner only for an approved revision", async () => {
    const loader = resolveLoader(version({ review_state: "approved" }));
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    await screen.findByTestId("visual-bible-entity-sheet");
    expect(
      screen.queryByTestId("visual-bible-candidate-only")
    ).not.toBeInTheDocument();
  });

  it("renders the append-only review history from the envelope", async () => {
    const loader = resolveLoader(
      version({
        review_events: [
          {
            action: "approve",
            actor_source: "human",
            actor: "owner",
            reason: "人工审查：批准",
            event_key: "ev-approve",
            from_review_state: "candidate",
            to_review_state: "approved",
          },
        ],
      })
    );
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    await screen.findByTestId("visual-bible-entity-sheet");
    expect(screen.getByTestId("visual-bible-review-history")).toBeInTheDocument();
    expect(screen.getByTestId("visual-bible-review-event")).toHaveAttribute(
      "data-action",
      "approve"
    );
  });

  it("submits an explicit review action only; the state returns from the server", async () => {
    mocks.review.mockResolvedValue({ data: version() });
    let calls = 0;
    const loader = vi.fn(async () => {
      calls += 1;
      return calls > 1
        ? version({ review_state: "approved" })
        : version({ review_state: "candidate" });
    });
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    const sheet = await screen.findByTestId("visual-bible-entity-sheet");
    expect(sheet).toHaveAttribute("data-review-state", "candidate");

    fireEvent.click(screen.getByTestId("visual-bible-review-action-approve"));

    await waitFor(() => expect(mocks.review).toHaveBeenCalledTimes(1));
    const [novelId, versionId, body] = mocks.review.mock.calls[0];
    expect(novelId).toBe("11");
    expect(versionId).toBe(1);
    expect(body.action).toBe("approve");
    expect(body.from_review_state).toBe("candidate");
    expect(body.actor_source).toBe("human");

    // review truth is not saved client-side; the server envelope re-drives it.
    await waitFor(() =>
      expect(screen.getByTestId("visual-bible-entity-sheet")).toHaveAttribute(
        "data-review-state",
        "approved"
      )
    );
  });

  it("locks review actions for a superseded revision", async () => {
    const loader = resolveLoader(version({ review_state: "superseded" }));
    render(<VisualBibleEntitySheet novelId="11" versionId={1} loader={loader} />);
    await screen.findByTestId("visual-bible-entity-sheet");
    expect(screen.getByTestId("visual-bible-review-locked")).toBeInTheDocument();
    expect(
      screen.queryByTestId("visual-bible-review-actions")
    ).not.toBeInTheDocument();
  });
});

describe("VisualReviewActions (explicit review action bar)", () => {
  it("submits only the explicit action for the current review state", () => {
    const onReview = vi.fn();
    render(
      <VisualReviewActions reviewState="candidate" onReview={onReview} />
    );
    const actions = [
      ...new Set(
        screen
          .getAllByTestId(/^visual-bible-review-action-/)
          .map((b) => b.getAttribute("data-action"))
      ),
    ].sort();
    expect(actions).toEqual([
      "approve",
      "edit",
      "needs_relink",
      "reject",
      "supersede",
    ]);
    fireEvent.click(screen.getByTestId("visual-bible-review-action-approve"));
    expect(onReview).toHaveBeenCalledWith("approve", undefined);
    expect(onReview).toHaveBeenCalledTimes(1);
  });

  it("forwards the optional audit reason with the action", () => {
    const onReview = vi.fn();
    render(
      <VisualReviewActions reviewState="candidate" onReview={onReview} />
    );
    fireEvent.change(screen.getByTestId("visual-bible-review-reason"), {
      target: { value: "rights cleared by uploader" },
    });
    fireEvent.click(screen.getByTestId("visual-bible-review-action-approve"));
    expect(onReview).toHaveBeenCalledWith("approve", "rights cleared by uploader");
  });

  it("does not send an empty reason", () => {
    const onReview = vi.fn();
    render(
      <VisualReviewActions reviewState="candidate" onReview={onReview} />
    );
    fireEvent.change(screen.getByTestId("visual-bible-review-reason"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByTestId("visual-bible-review-action-approve"));
    expect(onReview).toHaveBeenCalledWith("approve", undefined);
  });

  it("locks actions for a terminal review state", () => {
    render(
      <VisualReviewActions reviewState="superseded" onReview={vi.fn()} />
    );
    expect(screen.getByTestId("visual-bible-review-locked")).toBeInTheDocument();
    expect(
      screen.queryByTestId("visual-bible-review-actions")
    ).not.toBeInTheDocument();
  });

  it("disables every action while a submission is in flight", () => {
    const onReview = vi.fn();
    render(
      <VisualReviewActions
        reviewState="candidate"
        onReview={onReview}
        disabled
      />
    );
    const approve = screen.getByTestId(
      "visual-bible-review-action-approve"
    ) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    expect(
      (screen.getByTestId("visual-bible-review-reason") as HTMLInputElement)
        .disabled
    ).toBe(true);
    fireEvent.click(approve);
    expect(onReview).not.toHaveBeenCalled();
  });

  it("renders the action vocabulary in Chinese for human review", () => {
    render(
      <VisualReviewActions reviewState="candidate" onReview={vi.fn()} />
    );
    expect(screen.getByText(REVIEW_ACTION_LABEL_TEXT.approve)).toBeInTheDocument();
    expect(
      screen.getByText(REVIEW_ACTION_LABEL_TEXT.needs_relink)
    ).toBeInTheDocument();
  });
});

describe("ReferenceAssetStatus (rights/provenance gate)", () => {
  it("renders distinct rights badges and never shows an unapproved asset as approved", () => {
    const assets: VisualReferenceAssetView[] = [
      asset({ rights_status: "cleared", approved: true }),
      asset({
        asset_key: "ref-denied",
        asset_id: "asset-2",
        rights_status: "denied",
        approved: false,
      }),
      asset({ asset_key: "ref-pending", asset_id: "asset-3", rights_status: "pending" }),
    ];
    render(<ReferenceAssetStatus assets={assets} />);

    expect(screen.getAllByTestId("visual-bible-asset")).toHaveLength(3);
    const rights = [
      ...new Set(
        screen
          .getAllByTestId("visual-bible-asset-rights")
          .map((b) => b.getAttribute("data-rights"))
      ),
    ].sort();
    expect(rights).toEqual(["cleared", "denied", "pending"]);

    expect(screen.getAllByTestId("visual-bible-asset-approved")).toHaveLength(1);
    expect(screen.getAllByTestId("visual-bible-asset-not-approved")).toHaveLength(2);
    expect(screen.getByText(/权利未获授权 — 禁止使用该素材/)).toBeInTheDocument();
  });

  it("shows an explicit empty message when there are no reference assets", () => {
    render(<ReferenceAssetStatus assets={[]} />);
    expect(screen.getByTestId("visual-bible-assets-empty")).toHaveTextContent(
      "暂无参考素材"
    );
  });
});

describe("visual-bible-api helpers", () => {
  it("picks the latest candidate by version id without claiming canon", () => {
    const latest = pickLatestVisualBibleVersion([
      version({ id: 1, version_key: "v1" }),
      version({ id: 3, version_key: "v3" }),
      version({ id: 2, version_key: "v2" }),
    ]);
    expect(latest?.id).toBe(3);
    expect(pickLatestVisualBibleVersion([])).toBeNull();
  });

  it("shortens sha256 hashes for display", () => {
    expect(shortVisualHash(H(9))).toMatch(/^.{8}….{4}$/);
    expect(shortVisualHash("abc")).toBe("abc");
  });
});
