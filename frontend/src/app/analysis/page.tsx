"use client";

/**
 * 全局分析工作台 — Phase 20 Structure Workspace
 * 结构为脊梁；时间线 / 人物关系 / 线索与伏笔为挂载在选中结构节点上的 facets。
 * 不暴露中间摘要/主题/节奏；NM 始终为 candidate_preview（预览·未发布）。
 *
 * 本页为渲染协调层；数据流拆分见：
 * - hooks/use-timeline-workspace.ts —— 时间线数据流（选书/轮询/启停/筛选）
 * - hooks/use-structure-forest.ts —— 结构树/NM 数据流（树加载/claims/证据链接）
 * - components/analysis/workspace-view-tabs.tsx —— 视图/切片/版本 tabs + 全书确认
 * - lib/timeline-source.ts —— 时间线版本源纯函数
 */

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { BookOpen } from "lucide-react";
import { NovelPickerStrip } from "@/components/bookshelf/novel-picker-strip";

import { type AnalysisChapterRef } from "@/components/analysis/analysis-chat-panel";
import { AnalysisUnifiedChat } from "@/components/analysis/analysis-unified-chat";
import {
  AnalysisFacetTabs,
  AnalysisVersionTabs,
  AnalysisViewTabs,
  FullBookConfirmDialog,
  type AnalysisPageView,
  type AnalysisWorkspaceMode,
} from "@/components/analysis/workspace-view-tabs";
import { ClueWorkspace } from "@/components/clues/clue-workspace";
import { RelationshipWorkspace } from "@/components/relationships/relationship-workspace";
import { treeNodeToSelection } from "@/components/structure/build-structure-tree";
import type { StructureTreeNode } from "@/components/structure/structure-types";
import { StructureWorkspaceShell } from "@/components/structure/structure-workspace-shell";
import { TimelineChart } from "@/components/timeline/timeline-chart";
import { TimelineControls } from "@/components/timeline/timeline-controls";
import { TimelineStatus } from "@/components/timeline/timeline-status";
import { novelsApi, timelineApi, type Novel } from "@/lib/api";
import { useStructureForest } from "@/hooks/use-structure-forest";
import { useTimelineWorkspace } from "@/hooks/use-timeline-workspace";
import { cn } from "@/lib/utils";

function AnalysisWorkspace() {
  const searchParams = useSearchParams();
  const novelFromQuery = searchParams.get("novel") || "";

  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelId, setNovelId] = useState("");
  /** 章节 id/章号映射（对话锚点与剧透边界提示复用结构树的章节数据）。 */
  const [chapterList, setChapterList] = useState<AnalysisChapterRef[]>([]);
  /** Shared narrative chapter for relationship fold (server remains spoiler authority). */
  const [throughChapter, setThroughChapter] = useState<number | "">("");
  /**
   * 用户显式设置的剧透上限（关系/时间线点击）。
   * 结构树只响应显式上限；节点选择对 throughChapter 的自动对齐
   * 是 facet 过滤用途，不应让导航树收缩（否则点一章树就少一截）。
   */
  const [explicitThrough, setExplicitThrough] = useState<number | "">("");
  const [workspace, setWorkspace] = useState<AnalysisWorkspaceMode>("timeline");
  /** 顶层视图：默认对话；切换只做 CSS 隐藏，不卸载另一视图的状态。 */
  const [pageView, setPageView] = useState<AnalysisPageView>("chat");
  const [error, setError] = useState("");

  // 小说列表为页面顶层共享数据：timeline hook 选书时取阅读偏好，
  // structure hook 的 NM 树回退/剧透上限需要 chapter_count。
  useEffect(() => {
    novelsApi
      .list()
      .then((response) => setNovels(response.data.items))
      .catch(() => setError("无法加载小说列表"));
  }, []);

  const selectedNovel = novels.find((novel) => String(novel.id) === novelId);
  const chapterCount = selectedNovel?.chapter_count ?? 1;

  const structure = useStructureForest({
    novelId,
    throughChapter,
    explicitThrough,
    chapterCount,
    setChapterList,
  });

  const timeline = useTimelineWorkspace({
    novelId,
    novels,
    setNovelId,
    setThroughChapter,
    setExplicitThrough,
    setWorkspace,
    setError,
    selectedNodeRef: structure.selectedNodeRef,
    selectedNode: structure.selectedNode,
    structure: {
      prepareForNovel: structure.prepareForNovel,
      loadStructure: structure.loadStructure,
    },
  });

  useEffect(() => {
    if (!novelFromQuery || !novels.length) return;
    if (String(novelId) === String(novelFromQuery)) return;
    const exists = novels.some((n) => String(n.id) === String(novelFromQuery));
    if (exists) void timeline.selectNovel(String(novelFromQuery));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelFromQuery, novels]);

  function handleStructureSelect(node: StructureTreeNode) {
    const selection = treeNodeToSelection(node);
    structure.selectStructureNode(node);
    // Align relationship through with node end when user has not set a lower cap
    if (throughChapter === "" || throughChapter > selection.chapterEnd) {
      setThroughChapter(selection.chapterEnd);
    }
    // Re-fetch timeline with server-side structure chapter range (progressive poll uses ref).
    if (novelId) {
      timeline.resetEnvelopeSig();
      void timeline
        .loadTimeline(
          novelId,
          {
            ordering: timeline.ordering,
            person: timeline.person,
            causal: timeline.causal,
            fullBook: timeline.fullBook,
            chapterStart: selection.chapterStart,
            chapterEnd: selection.chapterEnd,
          },
          false
        )
        .catch(() => {
          /* keep previous envelope on transient failure */
        });
    }
  }

  return (
    <div
      data-testid="analysis-fullpage"
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      {/* 顶栏：横向书脊选书条（视觉）+ 原生 select（无障碍/测试，视觉隐藏） */}
      <header className="flex shrink-0 items-end gap-3 border-b border-border/40 px-3 pt-2 sm:px-4">
        <h1 className="sr-only">结构工作台</h1>
        <NovelPickerStrip
          novels={novels}
          value={novelId}
          onSelect={(id) => void timeline.selectNovel(id)}
        />
        <label className="sr-only">
          选择小说
          <select
            aria-label="选择小说"
            value={novelId}
            onChange={(event) => void timeline.selectNovel(event.target.value)}
          >
            <option value="">请选择一本小说</option>
            {novels.map((novel) => (
              <option key={novel.id} value={novel.id}>
                {novel.title}
              </option>
            ))}
          </select>
        </label>
      </header>

      {!novelId ? (
        <div className="grid min-h-0 flex-1 place-items-center p-8 text-center motion-transition-content">
          <div>
            <BookOpen className="mx-auto mb-3 size-8 text-primary" />
            <h2 className="font-serif text-2xl font-semibold">
              选择一本小说
            </h2>
          </div>
        </div>
      ) : (
        <StructureWorkspaceShell
          className="min-h-0 flex-1"
          structureSource={structure.structureSource}
          forest={structure.structureForest}
          selected={structure.selectedNode}
          onSelect={handleStructureSelect}
          claims={structure.nmClaims}
          claimsLoading={structure.nmClaimsLoading}
          claimsError={structure.nmClaimsError}
          selectedClaimId={structure.selectedClaimId}
          onClaimSelect={structure.onClaimSelect}
          sourceLinks={structure.nmSourceLinks}
          sourceLinksLoading={structure.nmSourceLinksLoading}
          sourceLinksError={structure.nmSourceLinksError}
          novelId={novelId}
        >
          <div className="flex h-full min-h-0 flex-col gap-4">
            {/* 顶层视图切换：对话（默认）| 分析可视化 —— 切换仅 CSS 隐藏，不卸载另一视图 */}
            <AnalysisViewTabs pageView={pageView} onPageViewChange={setPageView} />

            {/* 统一对话视图（默认）：读者对话 + 智能体回合（AI 自动路由）混排 */}
            <AnalysisUnifiedChat
              className={cn("min-h-0 flex-1", pageView !== "chat" && "hidden")}
              novelId={novelId}
              chapters={chapterList}
              fullBook={timeline.fullBook}
              progressChapterId={
                selectedNovel?.reading_progress?.chapter_id ?? null
              }
              selection={structure.selectedNode}
            />

            {/* 分析可视化视图（隐藏时保留状态：facet tab / 筛选 / 数据） */}
            <div
              data-testid="analysis-visualization-view"
              className={cn("grid gap-4", pageView !== "analysis" && "hidden")}
            >
              {/* Facet tabs — 分段控件（画布切换），保持 tab 语义 */}
              <AnalysisFacetTabs workspace={workspace} onWorkspaceChange={setWorkspace} />

              {workspace !== "clues" && (
                <TimelineStatus
                  run={timeline.run}
                  hasEvents={Boolean(
                    timeline.envelope.active?.events.length ||
                      timeline.envelope.running_candidate?.events.length
                  )}
                  onPause={() => void timeline.pauseRun()}
                  onResume={() => void timeline.retryRun()}
                  onStart={() => void timeline.startAnalysis()}
                  actionBusy={timeline.loading}
                />
              )}

              {workspace !== "clues" && (
                <div className="grid gap-3">
                  {workspace === "timeline" && (
                    <TimelineControls
                      ordering={timeline.ordering}
                      onOrderingChange={(value) =>
                        void timeline.updateQuery({ ordering: value })
                      }
                      people={timeline.people}
                      person={timeline.person}
                      onPersonChange={(value) =>
                        void timeline.updateQuery({ person: value })
                      }
                      causal={timeline.causal}
                      onCausalChange={(value) =>
                        void timeline.updateQuery({ causal: value })
                      }
                      fullBook={timeline.fullBook}
                      onFullBookRequest={(value) =>
                        value
                          ? timeline.setConfirmFullBook(true)
                          : void timelineApi
                              .setFullBookPreference(novelId, false)
                              .then(() => timeline.updateQuery({ fullBook: false }))
                      }
                    />
                  )}
                  {workspace === "relationships" && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                      <label className="flex items-center gap-2 text-amber-900/85">
                        <input
                          type="checkbox"
                          checked={timeline.fullBook}
                          onChange={(event) =>
                            event.target.checked
                              ? timeline.setConfirmFullBook(true)
                              : void timelineApi
                                  .setFullBookPreference(novelId, false)
                                  .then(() =>
                                    timeline.updateQuery({ fullBook: false })
                                  )
                          }
                          className="rounded border-border"
                        />
                        显示全书（可能剧透）
                      </label>
                    </div>
                  )}
                  {(timeline.envelope.active ||
                    timeline.envelope.running_candidate) && (
                    <AnalysisVersionTabs
                      envelope={timeline.envelope}
                      source={timeline.source}
                      run={timeline.run}
                      onSelectSource={timeline.selectSource}
                    />
                  )}
                </div>
              )}

              {workspace !== "clues" && error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}

              {/* 主区：时间轴 / 人物关系 / 线索（受结构节点章节范围约束） */}
              {workspace === "clues" ? (
                <ClueWorkspace
                  key={novelId}
                  novelId={novelId}
                  fullBook={timeline.fullBook}
                  chapterStart={structure.selectedNode?.chapterStart ?? null}
                  chapterEnd={structure.selectedNode?.chapterEnd ?? null}
                  onFullBookRequest={(enable) => {
                    if (enable) {
                      timeline.setConfirmFullBook(true);
                    } else {
                      void timelineApi
                        .setFullBookPreference(novelId, false)
                        .then(() => timeline.updateQuery({ fullBook: false }));
                    }
                  }}
                />
              ) : workspace === "relationships" ? (
                <RelationshipWorkspace
                  key={`${novelId}:${timeline.source}:${timeline.view?.version_id ?? "none"}:${structure.relationshipThroughChapter}`}
                  novelId={novelId}
                  source={timeline.source}
                  versionId={timeline.view?.version_id}
                  fullBook={timeline.fullBook}
                  throughChapter={structure.relationshipThroughChapter}
                  onThroughChapterChange={(value) => {
                    setThroughChapter(value);
                    setExplicitThrough(value);
                  }}
                  maxChapter={
                    structure.selectedNode
                      ? Math.min(
                          structure.selectedNode.chapterEnd,
                          selectedNovel?.chapter_count ??
                            structure.selectedNode.chapterEnd
                        )
                      : selectedNovel?.chapter_count
                  }
                />
              ) : timeline.loading && !timeline.view ? (
                <div
                  className="grid h-96 min-h-96 place-items-center rounded-3xl bg-muted motion-transition-content"
                  role="status"
                  aria-busy="true"
                  aria-label="正在加载时间线"
                >
                  <p className="text-sm text-muted-foreground">正在加载时间线…</p>
                </div>
              ) : timeline.view ? (
                <>
                  {timeline.multiChapterScope &&
                    structure.selectedNode &&
                    timeline.timelineDensity.truncated > 0 && (
                      <p
                        data-testid="timeline-multi-chapter-density"
                        className="sr-only"
                      >
                        <span data-testid="timeline-density-truncated">
                          truncated {timeline.timelineDensity.truncated}
                        </span>
                      </p>
                    )}
                  <TimelineChart
                    events={timeline.scopedEvents}
                    causalEdges={timeline.causal ? timeline.scopedCausalEdges : []}
                    ordering={timeline.ordering}
                    novelId={novelId}
                    onNarrativePositionChange={(chapter) => {
                      if (chapter != null) {
                        setThroughChapter(chapter);
                        setExplicitThrough(chapter);
                      }
                    }}
                  />
                </>
              ) : (
                <div className="grid min-h-48 place-items-center py-10 text-center text-muted-foreground">
                  <div>
                    <p className="text-sm">暂无时间线事件</p>
                    <p className="mt-1.5 text-xs">
                      点「开始分析」后，按章事件会显示在这里
                    </p>
                    <button
                      type="button"
                      disabled={timeline.loading}
                      onClick={() => void timeline.startAnalysis()}
                      className="mt-4 rounded-md bg-foreground px-4 py-2 text-sm text-background disabled:opacity-50"
                    >
                      开始分析
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </StructureWorkspaceShell>
      )}

      <FullBookConfirmDialog
        open={timeline.confirmFullBook}
        onCancel={() => timeline.setConfirmFullBook(false)}
        onConfirm={() => void timeline.enableFullBook()}
      />
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense
      fallback={
        <div className="grid min-h-72 place-items-center text-sm text-muted-foreground">
          加载分析工作台…
        </div>
      }
    >
      <AnalysisWorkspace />
    </Suspense>
  );
}
