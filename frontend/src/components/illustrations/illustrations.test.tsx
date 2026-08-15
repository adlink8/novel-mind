import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AssetRevisionView,
  ConsistencyReportView,
  IllustrationGalleryItemView,
  IllustrationGalleryResponse,
  IllustrationJobView,
  IllustrationReviewActionRequest,
  IllustrationReviewActionResponse,
  IllustrationReviewEnvelope,
} from "@/lib/illustrations-api";
import { shortIllustrationHash } from "@/lib/illustrations-api";
import {
  IllustrationApprovalActions,
  ILLUSTRATION_ACTION_LABEL_TEXT,
  ILLUSTRATION_STATE_LABEL_TEXT,
  IllustrationReviewHistory,
} from "./approval";
import { CONSISTENCY_VERDICT_LABEL, IllustrationCompare, JOB_STATUS_LABEL } from "./compare";
import { IllustrationGallery } from "./gallery";

/**
 * Phase 33-04 colocated vitest —— illustration review gallery workspace。
 * 覆盖 D-33-01..D-33-04 / REQ-VIS-04 的前端约束：
 * - 服务端 gallery envelope 渲染，错误/empty/loading 状态可见（无空成功）；
 * - 候选卡展示 job 状态/error/reason、approval state、proposal gate 原因、
 *   consistency verdict 与 append-only review history；
 * - 显式 approval action：前端只提交 action，review truth 由服务端返回；
 * - failed/unknown/paused job 显示显式重试，绝不显示为空成功；
 * - 谱系/对比抽屉展示 job/attempt/budget 证据与一致性报告；
 * - approval 状态机按钮随状态变化（superseded 锁定）。
 */

const H = (n: number) => String(n).repeat(64);

const asset = (over: Partial<AssetRevisionView> = {}): AssetRevisionView =>
  ({
    id: 1,
    owner_id: 1,
    novel_id: 11,
    job_id: 1,
    revision_key: "job-arin:rev1",
    revision_number: 1,
    asset_id: "asset-1",
    mime_type: "image/png",
    width: 1024,
    height: 1024,
    size_bytes: 4096,
    bytes_hash: H(1),
    scene_spec_hash: H(2),
    prompt_revision_id: 1,
    prompt_revision_hash: H(3),
    visual_bible_revision_hash: H(4),
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(5),
    cutoff_chapter: 8,
    provider: "mock",
    provider_model: "mock-img-v1",
    provider_request_id: "mock-req-1",
    rights_status: "cleared",
    approval_state: "candidate",
    ...over,
  }) as AssetRevisionView;

const job = (over: Partial<IllustrationJobView> = {}): IllustrationJobView =>
  ({
    id: 1,
    owner_id: 1,
    novel_id: 11,
    job_key: "job-arin",
    idempotency_key: H(6),
    status: "succeeded",
    status_reason: "generated",
    error_code: null,
    retry_count: 0,
    scene_spec_hash: H(2),
    prompt_revision_id: 1,
    prompt_revision_hash: H(3),
    visual_bible_revision_hash: H(4),
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(5),
    cutoff_chapter: 8,
    config_hash: H(7),
    price_snapshot: { provider: "mock", model: "mock-img-v1" },
    ...over,
  }) as IllustrationJobView;

const consistency = (over: Partial<ConsistencyReportView> = {}): ConsistencyReportView =>
  ({
    id: 1,
    owner_id: 1,
    novel_id: 11,
    asset_revision_id: 1,
    report_key: "arin:ch1",
    evaluator_id: "illustration-consistency.fixture.v1",
    evaluator_version: "1.0.0",
    model_lineage: {},
    fixture_set_hash: H(8),
    reference_asset_ids: ["ref-char-arin-1"],
    scores: { identity: 1.0, style: 1.0, negative_constraint_violations: 0 },
    verdict: "pass",
    details: {},
    idempotency_key: H(9),
    schema_version: "illustration-consistency.v1",
    created_at: null,
    ...over,
  }) as ConsistencyReportView;

const item = (
  over: Partial<IllustrationGalleryItemView> = {}
): IllustrationGalleryItemView =>
  ({
    asset: asset(),
    job: job(),
    consistency: consistency(),
    review_events: [],
    approval_gate: { ok: true, reason_code: null, detail: null },
    ...over,
  }) as IllustrationGalleryItemView;

const galleryResponse = (
  items: IllustrationGalleryItemView[]
): IllustrationGalleryResponse => ({ items, total: items.length });

const envelope = (over: Partial<IllustrationReviewEnvelope> = {}): IllustrationReviewEnvelope =>
  ({
    asset: asset(),
    job: job(),
    attempts: [
      {
        id: 1,
        attempt_number: 1,
        status: "succeeded",
        provider_request_id: "mock-req-1",
        request_hash: H(10),
        response_hash: H(1),
        usage: { input_tokens: 120, output_tokens: 1024 },
        cost_usd: "0.04000000",
        latency_ms: 12,
        error_code: null,
      },
    ],
    budget: {
      settled_calls: 1,
      settled_cost_usd: "0.04000000",
      reservation_status: "settled",
      settled_usage: {
        input_tokens: 120,
        output_tokens: 1024,
        cost_usd: "0.04",
        usage_unknown: false,
      },
      price_snapshot: { provider: "mock", model: "mock-img-v1" },
      ledger_max_calls: 10,
      ledger_max_cost_usd: "1.00000000",
    },
    consistency: consistency(),
    review_events: [],
    approval_gate: { ok: true, reason_code: null, detail: null },
    ...over,
  }) as IllustrationReviewEnvelope;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("IllustrationGallery (review workspace)", () => {
  it("shows a loading state before the server gallery resolves", async () => {
    let resolveFn: (v: IllustrationGalleryResponse) => void = () => undefined;
    const loader = vi.fn(
      () =>
        new Promise<IllustrationGalleryResponse>((res) => {
          resolveFn = res;
        })
    );
    render(<IllustrationGallery novelId="11" loader={loader} />);
    expect(screen.getByTestId("illustration-loading")).toBeInTheDocument();
    resolveFn(galleryResponse([item()]));
    await screen.findByTestId("illustration-gallery");
  });

  it("renders the error state when the loader fails (no silent empty success)", async () => {
    const loader = vi.fn(async () => {
      throw new Error("gate failed: rights_unresolved");
    });
    render(<IllustrationGallery novelId="11" loader={loader} />);
    const error = await screen.findByTestId("illustration-error");
    expect(error).toHaveTextContent("rights_unresolved");
  });

  it("renders an explicit empty state instead of empty-success", async () => {
    const loader = vi.fn(async () => galleryResponse([]));
    render(<IllustrationGallery novelId="11" loader={loader} />);
    expect(await screen.findByTestId("illustration-empty")).toHaveTextContent(
      "显示为空但不视为成功"
    );
  });

  it("renders the candidate-only banner and a candidate card with job status", async () => {
    const loader = vi.fn(async () => galleryResponse([item()]));
    render(<IllustrationGallery novelId="11" loader={loader} />);
    const gallery = await screen.findByTestId("illustration-gallery");
    expect(gallery).toBeInTheDocument();
    expect(screen.getByTestId("illustration-candidate-only")).toBeInTheDocument();
    const card = screen.getByTestId("illustration-card");
    expect(card).toHaveAttribute("data-approval-state", "candidate");
    expect(card).toHaveAttribute("data-job-status", "succeeded");
    expect(screen.getByTestId("illustration-approval-state")).toHaveTextContent(
      ILLUSTRATION_STATE_LABEL_TEXT.candidate
    );
    expect(screen.getByTestId("illustration-job-status")).toHaveTextContent(
      JOB_STATUS_LABEL.succeeded
    );
  });

  it("surfaces the fail-closed approval gate reason for a candidate", async () => {
    const blocked = item({
      approval_gate: {
        ok: false,
        reason_code: "rights_unresolved",
        detail: "asset rights_status 'unreviewed'; rights must be cleared",
      },
    });
    const loader = vi.fn(async () => galleryResponse([blocked]));
    render(<IllustrationGallery novelId="11" loader={loader} />);
    await screen.findByTestId("illustration-gallery");
    const gate = screen.getByTestId("illustration-approval-gate");
    expect(gate).toHaveAttribute("data-ok", "false");
    expect(gate).toHaveAttribute("data-reason-code", "rights_unresolved");
    expect(gate).toHaveTextContent("rights_unresolved");
  });

  it("shows the consistency verdict as a review signal, never approval", async () => {
    const loader = vi.fn(async () => galleryResponse([item()]));
    render(<IllustrationGallery novelId="11" loader={loader} />);
    await screen.findByTestId("illustration-gallery");
    const verdict = screen.getByTestId("illustration-consistency-verdict");
    expect(verdict).toHaveAttribute("data-verdict", "pass");
    expect(verdict).toHaveTextContent(CONSISTENCY_VERDICT_LABEL.pass);
  });

  it("submits an explicit approval action only; state comes from the server", async () => {
    const reviewAction = vi.fn(
      async (
        _novelId: string | number,
        _assetId: number,
        _body: IllustrationReviewActionRequest
      ): Promise<IllustrationReviewActionResponse> => ({
        asset: asset({ approval_state: "proposal_ready" }),
        envelope: envelope(),
      })
    );
    let calls = 0;
    const loader = vi.fn(async () => {
      calls += 1;
      if (calls > 1) {
        return galleryResponse([
          item({ asset: asset({ approval_state: "proposal_ready" }) }),
        ]);
      }
      return galleryResponse([item()]);
    });
    render(
      <IllustrationGallery
        novelId="11"
        loader={loader}
        reviewAction={reviewAction}
      />
    );
    await screen.findByTestId("illustration-gallery");

    fireEvent.click(screen.getByTestId("illustration-approval-approve"));

    await waitFor(() => expect(reviewAction).toHaveBeenCalledTimes(1));
    const [novelId, assetId, body] = reviewAction.mock.calls[0];
    expect(novelId).toBe("11");
    expect(assetId).toBe(1);
    expect(body.action).toBe("approve");
    expect(body.from_approval_state).toBe("candidate");
    expect(body.actor_source).toBe("human");

    // Review truth is not saved client-side; the server gallery re-drives it.
    await waitFor(() =>
      expect(
        screen
          .getByTestId("illustration-card")
          .getAttribute("data-approval-state")
      ).toBe("proposal_ready")
    );
  });

  it("submits the optional audit reason with the explicit action", async () => {
    const reviewAction = vi.fn(
      async (
        _novelId: string | number,
        _assetId: number,
        _body: IllustrationReviewActionRequest
      ): Promise<IllustrationReviewActionResponse> => ({
        asset: asset(),
        envelope: envelope(),
      })
    );
    const loader = vi.fn(async () => galleryResponse([item()]));
    render(
      <IllustrationGallery
        novelId="11"
        loader={loader}
        reviewAction={reviewAction}
      />
    );
    await screen.findByTestId("illustration-gallery");

    fireEvent.change(screen.getByTestId("illustration-approval-reason"), {
      target: { value: "符合正典形象" },
    });
    fireEvent.click(screen.getByTestId("illustration-approval-reject"));

    await waitFor(() => expect(reviewAction).toHaveBeenCalledTimes(1));
    const [, , body] = reviewAction.mock.calls[0];
    expect(body.action).toBe("reject");
    expect(body.reason).toBe("符合正典形象");
  });

  it("offers supersede/reject actions from proposal_ready and locks superseded", async () => {
    const loader = vi.fn(async () =>
      galleryResponse([
        item({ asset: asset({ approval_state: "proposal_ready" }) }),
        item({
          asset: asset({ id: 2, revision_number: 2, approval_state: "superseded" }),
          job: job({ id: 2, job_key: "job-superseded" }),
        }),
      ])
    );
    render(<IllustrationGallery novelId="11" loader={loader} />);
    await screen.findByTestId("illustration-gallery");

    const cards = screen.getAllByTestId("illustration-card");
    // proposal_ready card: reject + supersede + needs_relink available.
    expect(
      cards[0].querySelector('[data-testid="illustration-approval-supersede"]')
    ).not.toBeNull();
    expect(
      cards[0].querySelector('[data-testid="illustration-approval-approve"]')
    ).toBeNull();
    // superseded card: locked, no legal action remains.
    expect(cards[1].querySelector('[data-testid="illustration-approval-locked"]')).not.toBeNull();
  });

  it("surfaces failed jobs with an explicit retry (never an empty success)", async () => {
    const retryAction = vi.fn(
      async (
        _novelId: string | number,
        _jobId: number
      ): Promise<IllustrationJobView> => job({ status: "queued" })
    );
    let calls = 0;
    const loader = vi.fn(async () => {
      calls += 1;
      if (calls > 1) {
        return galleryResponse([
          item({
            asset: asset({ approval_state: "candidate" }),
            job: job({ status: "queued", status_reason: "re-queued", error_code: null }),
          }),
        ]);
      }
      return galleryResponse([
        item({
          job: job({
            status: "failed",
            status_reason: "provider returned an unusable asset",
            error_code: "empty_asset",
          }),
        }),
      ]);
    });
    render(
      <IllustrationGallery novelId="11" loader={loader} retryAction={retryAction} />
    );
    await screen.findByTestId("illustration-gallery");

    const card = screen.getByTestId("illustration-card");
    expect(card).toHaveAttribute("data-job-status", "failed");
    expect(screen.getByTestId("illustration-job-error")).toHaveTextContent(
      "empty_asset"
    );
    expect(screen.getByTestId("illustration-job-reason")).toHaveTextContent(
      "unusable asset"
    );

    fireEvent.click(screen.getByTestId("illustration-retry"));
    await waitFor(() => expect(retryAction).toHaveBeenCalledTimes(1));
    expect(retryAction.mock.calls[0][0]).toBe("11");
    expect(retryAction.mock.calls[0][1]).toBe(1);

    await waitFor(() =>
      expect(
        screen
          .getByTestId("illustration-card")
          .getAttribute("data-job-status")
      ).toBe("queued")
    );
  });

  it("expands the lineage/compare drawer with budget and attempt evidence", async () => {
    const envelopeLoader = vi.fn(async () => envelope());
    const loader = vi.fn(async () => galleryResponse([item()]));
    render(
      <IllustrationGallery
        novelId="11"
        loader={loader}
        envelopeLoader={envelopeLoader}
      />
    );
    await screen.findByTestId("illustration-gallery");

    fireEvent.click(screen.getByTestId("illustration-expand-lineage"));
    await waitFor(() => expect(envelopeLoader).toHaveBeenCalledTimes(1));
    expect(envelopeLoader.mock.calls[0]).toEqual(["11", 1]);

    expect(screen.getByTestId("illustration-compare")).toBeInTheDocument();
    expect(screen.getByTestId("illustration-lineage-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("illustration-budget-evidence")).toHaveTextContent(
      "已结算成本"
    );
    expect(screen.getByTestId("illustration-budget-evidence")).toHaveTextContent(
      "settled"
    );
    expect(screen.getByTestId("illustration-attempt")).toHaveTextContent(
      "尝试 #1 · succeeded"
    );
  });

  it("shows the append-only review history from the envelope", async () => {
    const review_events = [
      {
        event_key: "ev-1",
        action: "approve",
        actor_source: "human",
        actor: "owner",
        reason: "人工审查：批准",
        from_approval_state: "candidate",
        to_approval_state: "proposal_ready",
      },
    ] as const;
    const loader = vi.fn(async () =>
      galleryResponse([
        item({
          asset: asset({ approval_state: "proposal_ready" }),
          review_events: [...review_events],
        }),
      ])
    );
    render(<IllustrationGallery novelId="11" loader={loader} />);
    await screen.findByTestId("illustration-gallery");

    expect(screen.getByTestId("illustration-review-history")).toBeInTheDocument();
    const event = screen.getByTestId("illustration-review-event");
    expect(event).toHaveAttribute("data-action", "approve");
    expect(event).toHaveTextContent("candidate → proposal_ready");
  });
});

describe("IllustrationApprovalActions (presentational)", () => {
  it("renders legal actions for a candidate and forwards the action", () => {
    const onReview = vi.fn();
    render(
      <IllustrationApprovalActions approvalState="candidate" onReview={onReview} />
    );
    expect(screen.getByTestId("illustration-approval-approve")).toHaveTextContent(
      ILLUSTRATION_ACTION_LABEL_TEXT.approve
    );
    expect(screen.getByTestId("illustration-approval-reject")).toHaveTextContent(
      ILLUSTRATION_ACTION_LABEL_TEXT.reject
    );
    fireEvent.click(screen.getByTestId("illustration-approval-reject"));
    expect(onReview).toHaveBeenCalledWith("reject", undefined);
  });

  it("forwards the trimmed audit reason with the action", () => {
    const onReview = vi.fn();
    render(
      <IllustrationApprovalActions approvalState="candidate" onReview={onReview} />
    );
    fireEvent.change(screen.getByTestId("illustration-approval-reason"), {
      target: { value: "  负向约束冲突  " },
    });
    fireEvent.click(screen.getByTestId("illustration-approval-approve"));
    expect(onReview).toHaveBeenCalledWith("approve", "负向约束冲突");
  });

  it("locks a superseded candidate (no legal action remains)", () => {
    render(
      <IllustrationApprovalActions approvalState="superseded" onReview={vi.fn()} />
    );
    expect(screen.getByTestId("illustration-approval-locked")).toBeInTheDocument();
    expect(
      screen.queryByTestId("illustration-approval-approve")
    ).not.toBeInTheDocument();
  });
});

describe("IllustrationCompare (lineage + consistency compare)", () => {
  it("renders candidate lineage and the consistency report scores", () => {
    render(
      <IllustrationCompare
        job={job()}
        consistency={consistency()}
        attempts={envelope().attempts}
        budget={envelope().budget}
      />
    );
    expect(screen.getByTestId("illustration-compare")).toHaveAttribute(
      "data-job-status",
      "succeeded"
    );
    expect(screen.getByTestId("illustration-compare-report")).toHaveAttribute(
      "data-verdict",
      "pass"
    );
    expect(screen.getByTestId("illustration-consistency-verdict")).toHaveTextContent(
      CONSISTENCY_VERDICT_LABEL.pass
    );
    expect(screen.getByText(shortIllustrationHash(job().prompt_revision_hash))).toBeInTheDocument();
    expect(screen.getByTestId("illustration-budget-evidence")).toHaveTextContent(
      "0.04"
    );
  });

  it("shows an explicit missing-consistency state instead of a silent pass", () => {
    render(<IllustrationCompare job={job()} consistency={null} />);
    expect(
      screen.getByTestId("illustration-consistency-missing")
    ).toHaveTextContent("不可自动通过");
  });

  it("offers an explicit retry for a failed job and never for a succeeded one", () => {
    const onRetry = vi.fn();
    const { unmount } = render(
      <IllustrationCompare job={job({ status: "failed" })} consistency={null} onRetry={onRetry} />
    );
    fireEvent.click(screen.getByTestId("illustration-compare-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
    unmount();

    render(<IllustrationCompare job={job()} consistency={null} onRetry={onRetry} />);
    expect(
      screen.queryByTestId("illustration-compare-retry")
    ).not.toBeInTheDocument();
  });
});

describe("IllustrationReviewHistory (presentational)", () => {
  it("renders nothing when the history is empty", () => {
    render(<IllustrationReviewHistory events={[]} />);
    expect(
      screen.queryByTestId("illustration-review-history")
    ).not.toBeInTheDocument();
  });
});
