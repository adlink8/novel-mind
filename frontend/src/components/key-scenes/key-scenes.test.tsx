import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  SceneCandidateSetView,
  SceneCandidateView,
} from "@/lib/key-scenes-api";
import { shortKeySceneHash } from "@/lib/key-scenes-api";
import {
  ACTION_LABEL_TEXT,
  CandidateCard,
  REVIEW_STATE_LABEL_TEXT,
  REASON_LABEL_TEXT,
} from "./candidate-card";
import { KeySceneReviewWorkspace } from "./review";

/**
 * Phase 31-03 colocated vitest —— key-scene human review workspace。
 * 覆盖 D-31-01..D-31-05 / REQ-VIS-02 的前端约束：
 * - 服务端 envelope 渲染，错误/empty/loading 状态可见；
 * - 候选卡展示证据范围、salience 理由、diversity、score、坐标、cutoff 与 lineage；
 * - heuristic 元数据仅作诊断展示，不呈现为证据权威；
 * - 候选仅提交显式 action，review truth 由服务端返回，前端不保存；
 * - freeze 展示冻结 manifest（仅已批准候选），剧透安全的候选列表。
 */

const mocks = vi.hoisted(() => ({
  review: vi.fn(),
  freeze: vi.fn(),
  routerPush: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

vi.mock("@/lib/key-scenes-api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/key-scenes-api")>();
  return {
    ...mod,
    keyScenesApi: {
      ...mod.keyScenesApi,
      reviewCandidate: mocks.review,
      freeze: mocks.freeze,
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
  excerpt: "Arin drew his sword as the rain fell.",
  cutoff_chapter: 2,
  ...over,
});

const candidate = (
  over: Partial<SceneCandidateView> = {}
): SceneCandidateView =>
  ({
    candidate_key: "ks-v1-0",
    candidate_order: 0,
    scene_id: "scene-arin-1",
    chapter_id: 101,
    chapter_number: 1,
    source_start: 6,
    source_end: 10,
    source_hash: H(4),
    coordinates: { cast: ["arin", "mara"], place: "courtyard", time: "night", pov: "arin" },
    spoiler_cutoff: 2,
    salience_reasons: [
      { reason_code: "plot_turn", detail: "黎明进攻", score: 0.9 },
      { reason_code: "dialogue_turn", detail: "对话转折", score: 0.6 },
    ],
    score_total: 0.82,
    score_breakdown: { plot_turn: 0.9, dialogue_turn: 0.6 },
    diversity_key: H(7),
    detector_id: "key-scene.v1",
    detector_version: "1.0.0",
    policy_hash: H(8),
    evidence_ranges: [evidenceRef()],
    heuristic_signal: {
      availability: "available",
      speaker_offsets: [{ offset_start: 7, offset_end: 8, speaker_key: "arin" }],
      dialogue_offsets: [{ offset_start: 8, offset_end: 9 }],
      confidence: 0.9,
      warnings: [],
      detector_id: "key-scene.v1",
      detector_version: "1.0.0",
    },
    review_state: "candidate",
    ...over,
  }) as SceneCandidateView;

const quietCandidate = (over: Partial<SceneCandidateView> = {}) =>
  candidate({
    candidate_key: "ks-v1-1",
    candidate_order: 1,
    scene_id: "scene-harbor-2",
    chapter_id: 102,
    chapter_number: 2,
    coordinates: { cast: ["arin"], place: "harbor", time: "night", pov: "arin" },
    salience_reasons: [
      { reason_code: "quiet_emotional", detail: "安静情感", score: 0.7 },
      { reason_code: "diversity_quota", detail: "多样性配额", score: 0.5 },
    ],
    diversity_key: H(9),
    heuristic_signal: {
      availability: "unavailable",
      speaker_offsets: [],
      dialogue_offsets: [],
      confidence: null,
      warnings: ["no_dialogue_detected"],
      detector_id: "key-scene.v1",
      detector_version: "1.0.0",
    },
    ...over,
  }) as SceneCandidateView;

const setView = (over: Partial<SceneCandidateSetView> = {}): SceneCandidateSetView =>
  ({
    id: 1,
    owner_id: 1,
    novel_id: 11,
    version_key: "ks-main",
    revision_number: 1,
    parent_set_id: null,
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(2),
    cutoff_chapter: 2,
    schema_version: "key-scene.v1",
    schema_hash: H(6),
    policy_hash: H(8),
    detector_id: "key-scene.v1",
    detector_version: "1.0.0",
    manifest_hash: H(10),
    approved_visual_bible_revision_id: null,
    approved_visual_bible_revision_hash: null,
    review_state: "candidate",
    candidates: [candidate(), quietCandidate()],
    review_decisions: [],
    ...over,
  }) as SceneCandidateSetView;

const resolveLoader = (v: SceneCandidateSetView) => vi.fn(async () => v);

describe("KeySceneReviewWorkspace (candidate review envelope)", () => {
  it("shows a loading state before the server envelope resolves", async () => {
    let resolveFn: (v: SceneCandidateSetView) => void = () => undefined;
    const loader = vi.fn(
      () =>
        new Promise<SceneCandidateSetView>((res) => {
          resolveFn = res;
        })
    );
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    expect(screen.getByTestId("key-scene-loading")).toBeInTheDocument();
    resolveFn(setView());
    await screen.findByTestId("key-scene-workspace");
  });

  it("renders the error state when the loader fails (no silent empty success)", async () => {
    const loader = vi.fn(async () => {
      throw new Error("gate failed: evidence_missing");
    });
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    const error = await screen.findByTestId("key-scene-error");
    expect(error).toHaveTextContent("evidence_missing");
  });

  it("renders an explicit empty state instead of empty-success", async () => {
    const loader = resolveLoader(setView({ candidates: [] }));
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    expect(await screen.findByTestId("key-scene-empty")).toHaveTextContent(
      "显示为空但不视为成功"
    );
  });

  it("renders the candidate-only banner and review state for a candidate set", async () => {
    const loader = resolveLoader(setView());
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    const workspace = await screen.findByTestId("key-scene-workspace");
    expect(workspace).toHaveAttribute("data-review-state", "candidate");
    expect(screen.getByTestId("key-scene-candidate-only")).toBeInTheDocument();
    expect(screen.getAllByTestId("key-scene-candidate")).toHaveLength(2);
  });

  it("renders evidence, reasons, coordinates, score and cutoff on the card", async () => {
    const loader = resolveLoader(setView());
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    await screen.findByTestId("key-scene-workspace");

    const card = screen.getAllByTestId("key-scene-candidate")[0];
    expect(card).toHaveTextContent("第 1 章");
    expect(card).toHaveTextContent("评分 0.82");
    expect(screen.getAllByTestId("key-scene-evidence-panel")[0]).toHaveTextContent(
      "第 1 章 · 范围 6–10"
    );
    expect(screen.getAllByTestId("key-scene-evidence-panel")[0]).toHaveTextContent(
      "截止第 2 章"
    );
    expect(screen.getAllByTestId("key-scene-evidence-hash")[0]).toHaveTextContent(
      `hash ${H(3).slice(0, 8)}…`
    );
    // Reason vocabulary + diversity are server-provided and visible.
    expect(screen.getByText(REASON_LABEL_TEXT.plot_turn)).toBeInTheDocument();
    expect(screen.getAllByText(REASON_LABEL_TEXT.quiet_emotional).length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("key-scene-diversity").length).toBe(2);
  });

  it("marks the heuristic signal as diagnostic, never as citation authority", async () => {
    const loader = resolveLoader(setView());
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    await screen.findByTestId("key-scene-workspace");

    const available = screen.getAllByTestId("key-scene-heuristic")[0];
    expect(available).toHaveAttribute("data-availability", "available");
    expect(available).toHaveTextContent("仅诊断，非证据");
    expect(available).toHaveTextContent("置信度 0.90");
    // The unavailable quiet candidate keeps an explicit warning, never a score.
    const unavailable = screen.getAllByTestId("key-scene-heuristic")[1];
    expect(unavailable).toHaveAttribute("data-availability", "unavailable");
    expect(unavailable).toHaveTextContent("no_dialogue_detected");
  });

  it("routes the source jump to the reader chapter range", async () => {
    const loader = resolveLoader(setView());
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    await screen.findByTestId("key-scene-workspace");

    fireEvent.click(screen.getAllByTestId("key-scene-evidence-jump")[0]);
    expect(mocks.routerPush).toHaveBeenCalledWith(
      "/novels/11?chapter=101&start=6&from=key-scenes"
    );
  });

  it("submits an explicit candidate review action only; state returns from the server", async () => {
    mocks.review.mockResolvedValue({
      data: { set: setView() },
    });
    let calls = 0;
    const loader = vi.fn(async () => {
      calls += 1;
      if (calls > 1) {
        const base = setView();
        base.candidates = [
          candidate({ review_state: "approved" }),
          quietCandidate(),
        ];
        base.review_decisions = [
          {
            decision_key: "ds-1",
            action: "approve",
            actor_source: "human",
            actor: "owner",
            reason: "人工审查：批准",
            from_review_state: "candidate",
            to_review_state: "approved",
            candidate_key: "ks-v1-0",
          },
        ];
        return base;
      }
      return setView();
    });
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    await screen.findByTestId("key-scene-workspace");

    fireEvent.click(screen.getAllByTestId("key-scene-review-approve")[0]);

    await waitFor(() => expect(mocks.review).toHaveBeenCalledTimes(1));
    const [novelId, setId, body] = mocks.review.mock.calls[0];
    expect(novelId).toBe("11");
    expect(setId).toBe(1);
    expect(body.action).toBe("approve");
    expect(body.candidate_key).toBe("ks-v1-0");
    expect(body.from_review_state).toBe("candidate");
    expect(body.actor_source).toBe("human");

    // Review truth is not saved client-side; the server envelope re-drives it.
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("key-scene-candidate")[0]
          .getAttribute("data-review-state")
      ).toBe("approved")
    );
  });

  it("freezes the set and shows the approved-only frozen manifest", async () => {
    mocks.freeze.mockResolvedValue({
      data: {
        set: setView({ review_state: "approved" }),
        frozen: {
          id: 1,
          owner_id: 1,
          novel_id: 11,
          version_key: "ks-main",
          revision_number: 1,
          parent_set_id: null,
          source_snapshot_id: "ss-1",
          source_snapshot_hash: H(2),
          cutoff_chapter: 2,
          schema_version: "key-scene.v1",
          schema_hash: H(6),
          policy_hash: H(8),
          detector_id: "key-scene.v1",
          detector_version: "1.0.0",
          manifest_hash: H(11),
          approved_visual_bible_revision_id: null,
          approved_visual_bible_revision_hash: null,
          review_state: "approved",
          candidates: [candidate({ review_state: "approved" })],
          review_decisions: [],
        },
      },
    });
    let calls = 0;
    const loader = vi.fn(async () => {
      calls += 1;
      if (calls === 1) return setView();
      if (calls === 2) {
        // After the candidate approval reload: candidate approved, set still candidate.
        const base = setView();
        base.candidates = [
          candidate({ review_state: "approved" }),
          quietCandidate(),
        ];
        return base;
      }
      return setView({ review_state: "approved" });
    });
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    await screen.findByTestId("key-scene-workspace");

    // Approve one candidate so the freeze gate can pass.
    fireEvent.click(screen.getAllByTestId("key-scene-review-approve")[0]);
    await waitFor(() => expect(mocks.review).toHaveBeenCalledTimes(1));

    const workspace = await screen.findByTestId("key-scene-workspace");
    expect(workspace).toHaveAttribute("data-review-state", "candidate");

    // Wait until the reload lands the approved candidate so freeze is enabled.
    await waitFor(() =>
      expect(
        (screen.getByTestId("key-scene-freeze") as HTMLButtonElement).disabled
      ).toBe(false)
    );
    fireEvent.click(screen.getByTestId("key-scene-freeze"));
    await waitFor(() => expect(mocks.freeze).toHaveBeenCalledTimes(1));
    const [, , freezeBody] = mocks.freeze.mock.calls[0];
    expect(freezeBody.actor_source).toBe("human");

    // Frozen manifest shows only the approved candidate.
    await waitFor(() =>
      expect(screen.getByTestId("key-scene-frozen-manifest")).toBeInTheDocument()
    );
    expect(screen.getAllByTestId("key-scene-frozen-candidate")).toHaveLength(1);
    expect(screen.getByTestId("key-scene-frozen-candidate")).toHaveTextContent(
      "第 1 章"
    );
  });

  it("disables the freeze button until at least one candidate is approved", async () => {
    const loader = resolveLoader(setView());
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    await screen.findByTestId("key-scene-workspace");

    const freeze = screen.getByTestId("key-scene-freeze") as HTMLButtonElement;
    expect(freeze.disabled).toBe(true);
    expect(screen.getByText(/至少批准一个候选后才能冻结/)).toBeInTheDocument();
  });

  it("shows the append-only review history from the envelope", async () => {
    const base = setView();
    base.review_decisions = [
      {
        decision_key: "ds-history",
        action: "reject",
        actor_source: "human",
        actor: "owner",
        reason: "重复密度过高",
        from_review_state: "candidate",
        to_review_state: "rejected",
        candidate_key: "ks-v1-1",
      },
    ];
    const loader = resolveLoader(base);
    render(<KeySceneReviewWorkspace novelId="11" setId={1} loader={loader} />);
    await screen.findByTestId("key-scene-workspace");

    expect(screen.getByTestId("key-scene-review-history")).toBeInTheDocument();
    expect(screen.getByTestId("key-scene-review-event")).toHaveAttribute(
      "data-action",
      "reject"
    );
  });
});

describe("CandidateCard (presentational)", () => {
  it("renders reason vocabulary and review state labels", () => {
    render(
      <CandidateCard
        candidate={candidate()}
        onReview={vi.fn()}
      />
    );
    expect(screen.getByText(REVIEW_STATE_LABEL_TEXT.candidate)).toBeInTheDocument();
    expect(screen.getByText(REASON_LABEL_TEXT.plot_turn)).toBeInTheDocument();
    expect(screen.getByTestId("key-scene-review-approve")).toHaveTextContent(
      ACTION_LABEL_TEXT.approve
    );
    expect(screen.getByTestId("key-scene-review-reject")).toHaveTextContent(
      ACTION_LABEL_TEXT.reject
    );
  });

  it("does not offer review actions for a rejected candidate", () => {
    render(<CandidateCard candidate={candidate({ review_state: "rejected" })} onReview={vi.fn()} />);
    expect(screen.getByText(REVIEW_STATE_LABEL_TEXT.rejected)).toBeInTheDocument();
    expect(screen.queryByTestId("key-scene-review-approve")).not.toBeInTheDocument();
  });

  it("forwards the explicit action with the candidate key", () => {
    const onReview = vi.fn();
    render(<CandidateCard candidate={candidate()} onReview={onReview} />);
    fireEvent.click(screen.getByTestId("key-scene-review-reject"));
    expect(onReview).toHaveBeenCalledWith("reject", "ks-v1-0");
    expect(onReview).toHaveBeenCalledTimes(1);
  });

  it("never renders future-chapter metadata beyond the cutoff as a thumbnail", () => {
    // The server already filters past the cutoff; the card renders only the
    // provided envelope fields — no cover/thumbnail/downstream link fields exist.
    const c = candidate();
    expect(c.chapter_number).toBeLessThanOrEqual(c.spoiler_cutoff);
    render(<CandidateCard candidate={c} onReview={vi.fn()} />);
    expect(screen.queryByTestId("key-scene-thumbnail")).not.toBeInTheDocument();
  });
});
