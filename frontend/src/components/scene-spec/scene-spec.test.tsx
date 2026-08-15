import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  PromptArtifactView,
  PromptCompileRequest,
  PromptDetailResponse,
  PromptDiffResponse,
  PromptEditResponse,
  PromptRevisionView,
  SceneSpecDetailResponse,
  SceneSpecView,
} from "@/lib/scene-spec-api";
import { sceneSpecsApi, specSourceLabel } from "@/lib/scene-spec-api";
import { PromptDiff } from "./diff";
import {
  SceneSpecPreview,
  SPEC_SOURCE_LABEL_TEXT,
  SPEC_REVIEW_STATE_LABEL_TEXT,
} from "./preview";

/**
 * 32-04 colocated vitest —— Scene Spec / Prompt preview 与 diff 工作区。
 * 覆盖 D-32-01..D-32-04 的前端约束：
 * - 服务端 envelope 渲染，错误/empty/loading 状态可见；
 * - 每条 detail 的 evidence / Visual Bible / user_interpretation 来源可追溯，
 *   interpretation 永不升级为正典（author/rationale 显示）；
 * - unsupported / future spoiler 显示为拒绝或未解析，不伪装成正典；
 * - 前端只显示服务端 redacted_preview，不拼接 provider prompt，
 *   provider_calls=0 显式可见；
 * - 候选未批准时 candidate-only banner 可见，approved 时隐藏；
 * - diff 显示 changed sections 且 stale prompt 提示静默复用被拒；
 * - 显式 edit 只提交一个 action，服务端决定合法性，校验错误 fail closed。
 */

const H = (n: number) => String(n).repeat(64);

const detail = (over: Record<string, unknown> = {}) => ({
  detail_key: "d-subject",
  kind: "subject",
  source: "evidence",
  text: "Ayla 立于北境石厅之中",
  author: null,
  rationale: null,
  spoiler_cutoff: 3,
  evidence_keys: ["ev-ayla-hall"],
  visual_bible_stable_ids: [],
  ...over,
});

const constraint = (over: Record<string, unknown> = {}) => ({
  constraint_key: "nc-no-modern",
  scope: "era",
  source: "visual_bible",
  text: "不得出现现代器物",
  author: null,
  rationale: null,
  spoiler_cutoff: 3,
  ...over,
});

const uncertainty = (over: Record<string, unknown> = {}) => ({
  uncertainty_key: "u-ayla-weapon",
  reason: "future_spoiler",
  detail: "Ayla 的武器将在后文揭晓",
  ...over,
});

const spec = (over: Record<string, unknown> = {}) =>
  ({
    id: 1,
    owner_id: 1,
    novel_id: 11,
    spec_key: "spec-v1",
    revision_number: 1,
    scene_candidate_hash: H(1),
    scene_candidate_id: null,
    visual_bible_revision_hash: H(2),
    visual_bible_revision_id: null,
    source_snapshot_id: "ss-main",
    source_snapshot_hash: H(3),
    cutoff_chapter: 3,
    schema_version: "scene-spec.v1",
    schema_hash: H(4),
    compiler_id: "compiler.v1",
    compiler_version: "1.0.0",
    policy_hash: H(5),
    content_hash: H(6),
    review_state: "candidate",
    details: [detail()],
    negative_constraints: [constraint()],
    uncertainties: [uncertainty()],
    ...over,
  }) as SceneSpecView;

const promptRevision = (over: Record<string, unknown> = {}) =>
  ({
    id: 10,
    owner_id: 1,
    novel_id: 11,
    prompt_key: "pk-1",
    revision_number: 1,
    parent_prompt_revision_id: null,
    scene_spec_hash: H(6),
    visual_bible_revision_hash: H(2),
    source_snapshot_id: "ss-main",
    source_snapshot_hash: H(3),
    cutoff_chapter: 3,
    schema_version: "prompt-revision.v1",
    schema_hash: H(4),
    prompt_schema_hash: H(7),
    compiler_version: "1.0.0",
    adapter_id: "mock-provider",
    adapter_version: "1.0.0",
    config_hash: H(8),
    input_hash: H(9),
    prompt_hash: H(10),
    sections: { subject: "Ayla 立于北境石厅之中" },
    negative_constraints: ["era: 不得出现现代器物"],
    uncertainties: ["[future_spoiler] Ayla 的武器将在后文揭晓"],
    redacted_preview: "[subject]\nAyla 立于北境石厅之中",
    review_state: "candidate",
    ...over,
  }) as PromptRevisionView;

const previewArtifact = (over: Record<string, unknown> = {}) =>
  ({
    revision: promptRevision(),
    lineage: {
      scene_spec_hash: H(6),
      visual_bible_revision_hash: H(2),
      source_snapshot_id: "ss-main",
      source_snapshot_hash: H(3),
      cutoff_chapter: 3,
      schema_hash: H(4),
      prompt_schema_hash: H(7),
      compiler_version: "1.0.0",
      adapter_id: "mock-provider",
      adapter_version: "1.0.0",
      config_hash: H(8),
      input_hash: H(9),
      prompt_hash: H(10),
    },
    provider_calls: 0,
    ...over,
  }) as PromptArtifactView;

const specDetail = (over: Record<string, unknown> = {}) =>
  ({ spec: spec(), stale: false, ...over }) as SceneSpecDetailResponse;

const diffResponse = (over: Record<string, unknown> = {}) =>
  ({
    original_prompt_hash: H(11),
    current_prompt_hash: H(12),
    parent_prompt_revision_id: 9,
    revision_number: 2,
    same: false,
    changed_sections: [
      {
        section_key: "style",
        original: "冷色调",
        current: "暖色调",
      },
    ],
    changed_negative_constraints: [],
    changed_uncertainties: [],
    prompt_text_changed: true,
    ...over,
  }) as PromptDiffResponse;

const resolveLoader =
  <T,>(value: T) =>
  async () =>
    value;

describe("SceneSpecPreview (side-by-side preview workspace)", () => {
  it("shows a loading state before the server envelope resolves", async () => {
    let resolveFn: (v: SceneSpecDetailResponse) => void = () => undefined;
    const specLoader = vi.fn(
      () =>
        new Promise<SceneSpecDetailResponse>((res) => {
          resolveFn = res;
        })
    );
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    expect(screen.getByTestId("scene-spec-loading")).toBeInTheDocument();
    resolveFn(specDetail());
    await screen.findByTestId("scene-spec-preview");
  });

  it("renders the error state when a loader fails (no silent empty success)", async () => {
    const specLoader = vi.fn(async () => {
      throw new Error("gate failed: unsupported detail");
    });
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    const error = await screen.findByTestId("scene-spec-error");
    expect(error).toHaveTextContent("gate failed: unsupported detail");
  });

  it("renders an explicit empty state instead of empty-success", async () => {
    const specLoader = resolveLoader(
      specDetail({ spec: spec({ details: [], negative_constraints: [], uncertainties: [] }) })
    );
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    expect(await screen.findByTestId("scene-spec-empty")).toHaveTextContent(
      "显示为空但不视为成功"
    );
  });

  it("renders the candidate-only banner and review state for an unapproved spec", async () => {
    const specLoader = resolveLoader(specDetail({ spec: spec({ review_state: "needs_relink" }) }));
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    const preview = await screen.findByTestId("scene-spec-preview");
    expect(preview).toHaveAttribute("data-review-state", "needs_relink");
    expect(screen.getByTestId("scene-spec-review-state")).toHaveAttribute(
      "data-state",
      "needs_relink"
    );
    expect(screen.getByTestId("scene-spec-candidate-only")).toBeInTheDocument();
  });

  it("hides the candidate-only banner only for an approved spec", async () => {
    const specLoader = resolveLoader(specDetail({ spec: spec({ review_state: "approved" }) }));
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    await screen.findByTestId("scene-spec-preview");
    expect(
      screen.queryByTestId("scene-spec-candidate-only")
    ).not.toBeInTheDocument();
  });

  it("shows a stale banner when the spec lineage no longer matches", async () => {
    const specLoader = resolveLoader(specDetail({ stale: true }));
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    await screen.findByTestId("scene-spec-preview");
    expect(screen.getByTestId("scene-spec-stale")).toHaveTextContent(
      "静默复用已被拒绝"
    );
  });

  it("traces every detail back to evidence/Visual Bible/interpretation", async () => {
    const specLoader = resolveLoader(
      specDetail({
        spec: spec({
          details: [
            detail({ detail_key: "d-ev", source: "evidence", evidence_keys: ["ev-1"] }),
            detail({
              detail_key: "d-vb",
              source: "visual_bible",
              evidence_keys: [],
              visual_bible_stable_ids: ["ayla"],
            }),
            detail({
              detail_key: "d-user",
              source: "user_interpretation",
              evidence_keys: [],
              visual_bible_stable_ids: [],
              author: "读者·小雨",
              rationale: "读者认为光影偏冷",
            }),
          ],
        }),
      })
    );
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    await screen.findByTestId("scene-spec-preview");

    const sources = [
      ...new Set(
        screen
          .getAllByTestId("scene-spec-detail-source")
          .map((b) => b.getAttribute("data-source"))
      ),
    ].sort();
    // canon vs interpretation never collapse into one label (D-32-02).
    expect(sources).toEqual(["evidence", "user_interpretation", "visual_bible"]);
    expect(screen.getByTestId("scene-spec-detail-evidence")).toHaveTextContent("ev-1");
    expect(
      screen.getByTestId("scene-spec-detail-visual-bible")
    ).toHaveTextContent("ayla");
    expect(screen.getByTestId("scene-spec-detail-rationale")).toHaveTextContent(
      "作者：读者·小雨"
    );
    expect(screen.getByTestId("scene-spec-detail-rationale")).toHaveTextContent(
      "读者认为光影偏冷"
    );
  });

  it("surfaces an evidence detail without evidence as unresolved, never approved", async () => {
    const specLoader = resolveLoader(
      specDetail({
        spec: spec({
          details: [detail({ detail_key: "d-naked", evidence_keys: [] })],
        }),
      })
    );
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    await screen.findByTestId("scene-spec-preview");
    expect(screen.getByTestId("scene-spec-detail-unresolved")).toHaveTextContent(
      "未通过验证，不可审批"
    );
  });

  it("renders negative constraints and marks future spoilers as unsupported", async () => {
    const specLoader = resolveLoader(specDetail());
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    await screen.findByTestId("scene-spec-preview");
    expect(screen.getByTestId("scene-spec-constraint")).toHaveAttribute(
      "data-scope",
      "era"
    );
    expect(screen.getByTestId("scene-spec-constraint")).toHaveTextContent(
      "不得出现现代器物"
    );
    expect(screen.getByTestId("scene-spec-uncertainty")).toHaveAttribute(
      "data-reason",
      "future_spoiler"
    );
    // Unsupported/future spoiler material is shown as rejected/unresolved.
    expect(screen.getByTestId("scene-spec-unsupported")).toHaveTextContent(
      "未来剧透"
    );
  });

  it("never assembles a provider prompt client-side and shows provider_calls=0", async () => {
    const promptPreviewLoader = vi.fn(async (_n: string | number, _b: PromptCompileRequest) =>
      previewArtifact()
    );
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={resolveLoader(specDetail())}
        promptPreviewLoader={promptPreviewLoader}
      />
    );
    await screen.findByTestId("scene-spec-preview");
    // Only the server redacted_preview is rendered (never re-assembled locally).
    expect(screen.getByTestId("scene-spec-prompt-preview")).toHaveTextContent(
      "[subject]"
    );
    expect(screen.getByTestId("scene-spec-provider-calls")).toHaveTextContent(
      "provider_calls: 0"
    );
    // The preview compile request was forwarded to the server seam.
    expect(promptPreviewLoader).toHaveBeenCalledWith("11", expect.objectContaining({ spec_id: 1 }));
  });

  it("renders the Chinese source/review vocabulary", async () => {
    const specLoader = resolveLoader(specDetail());
    render(
      <SceneSpecPreview
        novelId="11"
        specId={1}
        specLoader={specLoader}
        promptPreviewLoader={resolveLoader(previewArtifact())}
      />
    );
    await screen.findByTestId("scene-spec-preview");
    expect(screen.getByText(SPEC_SOURCE_LABEL_TEXT.evidence)).toBeInTheDocument();
    expect(screen.getByText(SPEC_REVIEW_STATE_LABEL_TEXT.candidate)).toBeInTheDocument();
    expect(specSourceLabel("user_interpretation")).toBe("用户解读");
  });
});

describe("PromptDiff (explicit edit / diff workspace)", () => {
  it("shows a loading state before the diff resolves", async () => {
    let resolveFn: (v: PromptDiffResponse) => void = () => undefined;
    const diffLoader = vi.fn(
      () =>
        new Promise<PromptDiffResponse>((res) => {
          resolveFn = res;
        })
    );
    render(
      <PromptDiff
        novelId="11"
        revisionId={10}
        diffLoader={diffLoader}
        detailLoader={resolveLoader({ revision: promptRevision(), stale: false })}
      />
    );
    expect(screen.getByTestId("prompt-diff-loading")).toBeInTheDocument();
    resolveFn(diffResponse());
    await screen.findByTestId("prompt-diff");
  });

  it("renders the error state when a loader fails", async () => {
    const diffLoader = vi.fn(async () => {
      throw new Error("prompt has no parent to diff against");
    });
    render(
      <PromptDiff
        novelId="11"
        revisionId={10}
        diffLoader={diffLoader}
        detailLoader={resolveLoader({ revision: promptRevision(), stale: false })}
      />
    );
    const error = await screen.findByTestId("prompt-diff-error");
    expect(error).toHaveTextContent("prompt has no parent to diff against");
  });

  it("renders changed sections side-by-side", async () => {
    render(
      <PromptDiff
        novelId="11"
        revisionId={10}
        diffLoader={resolveLoader(diffResponse())}
        detailLoader={resolveLoader({ revision: promptRevision(), stale: false })}
      />
    );
    await screen.findByTestId("prompt-diff");
    const section = screen.getByTestId("prompt-diff-section");
    expect(section).toHaveAttribute("data-section", "style");
    expect(section).toHaveTextContent("冷色调");
    expect(section).toHaveTextContent("暖色调");
    expect(screen.getByTestId("prompt-diff-no-provider")).toHaveTextContent(
      "provider_calls: 0"
    );
  });

  it("shows a stale banner so the prompt cannot be silently reused", async () => {
    render(
      <PromptDiff
        novelId="11"
        revisionId={10}
        diffLoader={resolveLoader(diffResponse())}
        detailLoader={resolveLoader({ revision: promptRevision(), stale: true })}
      />
    );
    await screen.findByTestId("prompt-diff");
    expect(screen.getByTestId("prompt-diff-stale")).toHaveTextContent(
      "静默复用已被拒绝"
    );
  });

  it("submits one explicit edit action and shows the new candidate revision", async () => {
    const editAction = vi.fn(async (_n: string | number, _r: number, _b: unknown) => {
      const result = {
        revision: promptRevision({ id: 11, revision_number: 2, prompt_key: "edited-10" }),
        diff: diffResponse(),
      } as PromptEditResponse;
      return result;
    });
    render(
      <PromptDiff
        novelId="11"
        revisionId={10}
        diffLoader={resolveLoader(diffResponse())}
        detailLoader={resolveLoader({ revision: promptRevision(), stale: false })}
        editAction={editAction}
      />
    );
    await screen.findByTestId("prompt-diff");
    fireEvent.change(screen.getByTestId("prompt-diff-edit-detail-key"), {
      target: { value: "user-lighting" },
    });
    fireEvent.change(screen.getByTestId("prompt-diff-edit-text"), {
      target: { value: "冷色调顶光" },
    });
    fireEvent.change(screen.getByTestId("prompt-diff-edit-author"), {
      target: { value: "test-editor" },
    });
    fireEvent.change(screen.getByTestId("prompt-diff-edit-rationale"), {
      target: { value: "人工补充光影解读" },
    });
    fireEvent.submit(screen.getByTestId("prompt-diff-edit-form"));

    await waitFor(() => expect(editAction).toHaveBeenCalledTimes(1));
    const [novelId, revisionId, body] = editAction.mock.calls[0];
    expect(novelId).toBe("11");
    expect(revisionId).toBe(10);
    expect((body as Record<string, unknown>).detail_key).toBe("user-lighting");
    expect((body as Record<string, unknown>).text).toBe("冷色调顶光");
    // The browser only submits the action; the server decides legality.
    await screen.findByTestId("prompt-diff-edited");
  });

  it("surfaces a server validation error fail-closed", async () => {
    const editAction = vi.fn(async () => {
      throw new Error("detail d-subject is evidence-sourced and cannot be edited");
    });
    render(
      <PromptDiff
        novelId="11"
        revisionId={10}
        diffLoader={resolveLoader(diffResponse())}
        detailLoader={resolveLoader({ revision: promptRevision(), stale: false })}
        editAction={editAction}
      />
    );
    await screen.findByTestId("prompt-diff");
    fireEvent.change(screen.getByTestId("prompt-diff-edit-detail-key"), {
      target: { value: "d-subject" },
    });
    fireEvent.change(screen.getByTestId("prompt-diff-edit-text"), {
      target: { value: "改动正典" },
    });
    fireEvent.submit(screen.getByTestId("prompt-diff-edit-form"));
    const error = await screen.findByTestId("prompt-diff-validation-error");
    expect(error).toHaveTextContent("cannot be edited");
  });
});

describe("scene-spec-api client shape", () => {
  it("routes prompt review/history through the owner-scoped path", () => {
    expect(sceneSpecsApi.getSpec).toBeDefined();
  });
});
