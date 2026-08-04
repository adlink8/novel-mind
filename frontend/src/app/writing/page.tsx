"use client";

/**
 * 创作中心 - app/writing/page.tsx（Phase 36-02, D-36-01..D-36-03）
 *
 * 宿主页：显式选择原作 → 显式选择 Canon Fork（创建项目时）→ 进入
 * MarkdownEditor。任何路径都不读取阅读进度 / 阅读页面，fork 永远由用户
 * 显式选择（D-36-01）；所有项目都只属于 fanfiction_canon，页面不暴露
 * Original Canon / User Interpretation 写入口（D-36-03）。
 */

import { useCallback, useEffect, useState } from "react";

import { GitBranch, Loader2, Plus } from "lucide-react";

import { MarkdownEditor } from "@/components/writing/markdown-editor";
import { ExportPanel } from "@/components/writing/export-panel";
import { VisualReviewPanel } from "@/components/writing/visual-review-panel";
import { PageContainer, PageHeader } from "@/components/page-header";
import { novelsApi, type Novel } from "@/lib/api";
import {
  derivativeApi,
  type CanonForkView,
  type DerivativeChapterView,
  type DerivativeProjectView,
} from "@/lib/derivative-api";

export default function WritingPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelId, setNovelId] = useState<number | null>(null);
  const [projects, setProjects] = useState<DerivativeProjectView[]>([]);
  const [forks, setForks] = useState<CanonForkView[]>([]);
  const [project, setProject] = useState<DerivativeProjectView | null>(null);
  const [chapters, setChapters] = useState<DerivativeChapterView[]>([]);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectForkId, setNewProjectForkId] = useState<number | "">("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("加载书架中…");

  // Phase 36-04 refresh recovery: remember the selected project per novel so a
  // page reload restores the working project instead of always the first one.

  const selectProject = useCallback((next: DerivativeProjectView | null) => {
    setProject(next);
    setChapters([]);
    setError(null);
    if (next) {
      window.sessionStorage.setItem(
        `novelmind:writing:project:${next.novel_id}`,
        String(next.id)
      );
    }
  }, []);

  // 1. Load the owner's shelf.
  useEffect(() => {
    novelsApi
      .list()
      .then((res) => {
        const items = res.data.items;
        setNovels(items);
        setStatus(items.length ? "选择一本原作开始创作" : "请先导入一本原作");
        if (items[0]) setNovelId(items[0].id);
      })
      .catch(() => setStatus("书架加载失败，请稍后重试"));
  }, []);

  // 2. Explicit novel selection loads its derivative projects AND the explicit
  //    fork candidates for the creation picker (D-36-01: never inferred).
  useEffect(() => {
    if (novelId == null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- clear the previous novel's editor state before loading
    setProjects([]);
    setProject(null);
    setChapters([]);
    Promise.all([
      derivativeApi.listProjects(novelId),
      derivativeApi.listForks(novelId),
    ])
      .then(([projectRes, forkRes]) => {
        const items = projectRes.data.items;
        setProjects(items);
        setForks(forkRes.data.forks);
        setStatus(
          items.length
            ? "选择或创建 derivative project 开始写作"
            : "还没有项目；用显式 fork 创建一个"
        );
        // Refresh recovery: restore the project selected before the reload when
        // it still exists in this novel's owner-scoped list.
        const remembered = window.sessionStorage.getItem(
          `novelmind:writing:project:${novelId}`
        );
        selectProject(
          items.find((item) => String(item.id) === remembered) ?? items[0] ?? null
        );
      })
      .catch(() => setStatus("项目或 fork 加载失败，请稍后重试"));
    // selectProject is a stable useCallback([]); the novel switch runs once.
  }, [novelId, selectProject]);

  // 3. Project selection loads its ordered chapter plan.
  useEffect(() => {
    if (!novelId || !project) return;
    derivativeApi
      .listChapters(novelId, project.id)
      .then((res) => {
        setChapters(res.data.items);
        setStatus("草稿就绪，可以开始写作");
      })
      .catch(() => setStatus("章节计划加载失败，请稍后重试"));
  }, [novelId, project]);

  const createProject = async () => {
    if (!novelId) return;
    if (!newProjectName.trim()) {
      setError("请填写项目名称");
      return;
    }
    if (!newProjectForkId) {
      setError("必须显式选择一个 Canon Fork（不会从阅读页面推断）");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const res = await derivativeApi.createProject(novelId, {
        fork_id: Number(newProjectForkId),
        name: newProjectName.trim(),
      });
      const created = res.data.project;
      setProjects((current) => [created, ...current]);
      selectProject(created);
      setNewProjectName("");
      setNewProjectForkId("");
      setStatus("项目已创建并绑定到所选 fork");
    } catch {
      setError("创建项目失败，请确认 fork 仍可用后重试");
    } finally {
      setCreating(false);
    }
  };

  const handleChaptersChange = useCallback((next: DerivativeChapterView[]) => {
    setChapters(next);
  }, []);

  return (
    <PageContainer className="space-y-8">
      <PageHeader
        eyebrow="Derivative studio"
        title="创作中心"
        description="显式选择 Canon Fork 与 derivative project，规划章节并用 Markdown 编写只属于 Fanfiction Canon 的草稿。"
      />

      {error && (
        <div
          role="alert"
          className="rounded-2xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-600"
        >
          {error}
        </div>
      )}

      <section className="rounded-3xl border border-border bg-secondary/40 p-5 sm:p-6">
        <div className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <label className="block text-xs font-semibold text-muted-foreground">
              原作
              <select
                className="mt-2 w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                value={novelId ?? ""}
                onChange={(event) =>
                  setNovelId(event.target.value ? Number(event.target.value) : null)
                }
              >
                <option value="">选择原作</option>
                {novels.map((novel) => (
                  <option key={novel.id} value={novel.id}>
                    {novel.title}
                  </option>
                ))}
              </select>
            </label>

            <div className="rounded-2xl border border-border/70 p-3">
              <p className="text-xs font-semibold text-muted-foreground">新建项目</p>
              <input
                className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                value={newProjectName}
                onChange={(event) => setNewProjectName(event.target.value)}
                aria-label="新项目名称"
                placeholder="项目名称"
              />
              <label className="mt-3 block text-xs font-semibold text-muted-foreground">
                Canon Fork（显式选择）
                <select
                  className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  value={newProjectForkId}
                  onChange={(event) =>
                    setNewProjectForkId(
                      event.target.value ? Number(event.target.value) : ""
                    )
                  }
                >
                  <option value="">选择 fork</option>
                  {forks.map((fork) => (
                    <option key={fork.id} value={fork.id}>
                      {fork.fork_key} · v{fork.source_version_key}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
                onClick={() => void createProject()}
                disabled={creating || novelId == null}
              >
                {creating ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Plus className="size-4" />
                )}
                创建项目
              </button>
              {forks.length === 0 && (
                <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
                  当前原作还没有 Canon Fork；先通过 Fork 合约创建一个再写草稿。
                </p>
              )}
            </div>

            <div className="space-y-2" aria-label="项目列表">
              {projects.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`flex w-full items-start gap-2 rounded-xl border px-3 py-2 text-left text-sm transition-colors ${
                    project?.id === item.id
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-secondary/60"
                  }`}
                  onClick={() => selectProject(item)}
                >
                  <GitBranch className="mt-0.5 size-4 shrink-0 text-primary" />
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">{item.name}</span>
                    <span className="block truncate font-mono text-[11px] text-muted-foreground">
                      {item.fork_key} · {item.status}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <div className="min-w-0">
            {project ? (
              <MarkdownEditor
                novelId={novelId!}
                project={project}
                chapters={chapters}
                onChaptersChange={handleChaptersChange}
              />
            ) : (
              <div className="rounded-3xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
                <GitBranch className="mx-auto size-8 text-primary/60" />
                <p className="mt-3">
                  选择左侧项目，或用显式 Canon Fork 创建一个新的项目开始写作。
                </p>
              </div>
            )}
          </div>
        </div>
        <p className="mt-5 text-xs text-muted-foreground">{status}</p>
      </section>

      {novelId != null ? (
        <section className="rounded-3xl border border-border bg-secondary/40 p-5 sm:p-6">
          <VisualReviewPanel novelId={novelId} />
        </section>
      ) : null}

      {novelId != null && project ? (
        <section className="rounded-3xl border border-border bg-secondary/40 p-5 sm:p-6">
          <ExportPanel novelId={novelId} projectId={project.id} />
        </section>
      ) : null}
    </PageContainer>
  );
}
