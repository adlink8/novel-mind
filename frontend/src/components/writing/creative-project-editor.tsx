"use client";

import { useEffect, useMemo, useState } from "react";

import {
  fanfictionApi,
  novelsApi,
  type FanFiction,
  type FanFictionChapter,
  type FanFictionOverride,
  type FanFictionRevision,
  type Novel,
} from "@/lib/api";

export function CreativeProjectEditor() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [selectedNovelId, setSelectedNovelId] = useState<number | null>(null);
  const [projects, setProjects] = useState<FanFiction[]>([]);
  const [project, setProject] = useState<FanFiction | null>(null);
  const [chapters, setChapters] = useState<FanFictionChapter[]>([]);
  const [selectedChapterId, setSelectedChapterId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [chapterContent, setChapterContent] = useState("");
  const [revisions, setRevisions] = useState<FanFictionRevision[]>([]);
  const [overrides, setOverrides] = useState<FanFictionOverride[]>([]);
  const [overrideKey, setOverrideKey] = useState("");
  const [overrideStatement, setOverrideStatement] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [diffText, setDiffText] = useState("");
  const [status, setStatus] = useState("正在加载书架…");

  useEffect(() => {
    void novelsApi.list().then((response) => {
      const items = response.data.items;
      setNovels(items);
      if (items[0]) setSelectedNovelId(items[0].id);
      setStatus(items.length ? "选择一个原作开始编辑" : "请先导入一本原作");
    }).catch(() => setStatus("书架加载失败，请稍后重试"));
  }, []);

  useEffect(() => {
    if (!selectedNovelId) return;
    void fanfictionApi.list(selectedNovelId).then((response) => {
      setProjects(response.data);
      selectProject(response.data[0] ?? null);
      setStatus(response.data.length ? "草稿已就绪" : "还没有草稿，可以创建一个分支");
    }).catch(() => setStatus("草稿加载失败，请稍后重试"));
  }, [selectedNovelId]);

  useEffect(() => {
    if (!project) return;
    void Promise.all([
      fanfictionApi.chapters(project.id),
      fanfictionApi.revisions(project.id),
      fanfictionApi.overrides(project.id),
    ])
      .then(([chapterResponse, revisionResponse, overrideResponse]) => {
        setChapters(chapterResponse.data);
        setSelectedChapterId(chapterResponse.data[0]?.id ?? null);
        setChapterContent(chapterResponse.data[0]?.content ?? "");
        setRevisions(revisionResponse.data);
        setOverrides(overrideResponse.data);
      })
      .catch(() => setStatus("章节或版本历史加载失败，请稍后重试"));
  }, [project]);

  const selectedChapter = useMemo(
    () => chapters.find((chapter) => chapter.id === selectedChapterId) ?? null,
    [chapters, selectedChapterId]
  );

  useEffect(() => {
    if (!project || (title === project.title && content === (project.content ?? ""))) return;
    const timer = window.setTimeout(async () => {
      try {
        const response = await fanfictionApi.update(project.id, { title, content });
        setProject(response.data);
        setProjects((current) => current.map((item) => item.id === response.data.id ? response.data : item));
        const revisionResponse = await fanfictionApi.revisions(project.id);
        setRevisions(revisionResponse.data);
        setStatus("已自动保存");
      } catch {
        setStatus("自动保存失败，请点击保存项目重试");
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [content, project, title]);

  useEffect(() => {
    if (!project || !selectedChapter || chapterContent === (selectedChapter.content ?? "")) return;
    const timer = window.setTimeout(async () => {
      try {
        const response = await fanfictionApi.updateChapter(project.id, selectedChapter.id, { content: chapterContent });
        setChapters((current) => current.map((item) => item.id === response.data.id ? response.data : item));
        const revisionResponse = await fanfictionApi.revisions(project.id);
        setRevisions(revisionResponse.data);
        setStatus("章节已自动保存");
      } catch {
        setStatus("章节自动保存失败，请点击保存章节重试");
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [chapterContent, project, selectedChapter]);

  function selectProject(nextProject: FanFiction | null) {
    setProject(nextProject);
    setTitle(nextProject?.title ?? "");
    setContent(nextProject?.content ?? "");
    setChapters([]);
    setSelectedChapterId(null);
    setChapterContent("");
    setRevisions([]);
    setDiffText("");
    setOverrides([]);
    setOverrideKey("");
    setOverrideStatement("");
    setOverrideReason("");
  }

  async function createProject() {
    if (!selectedNovelId) return;
    const response = await fanfictionApi.create({ novel_id: selectedNovelId, title: "未命名分支" });
    setProjects((current) => [response.data, ...current]);
    selectProject(response.data);
    setStatus("草稿已创建");
  }

  async function saveProject() {
    if (!project) return;
    const response = await fanfictionApi.update(project.id, { title, content });
    setProject(response.data);
    setProjects((current) => current.map((item) => item.id === response.data.id ? response.data : item));
    setStatus("项目已保存");
  }

  async function createChapter() {
    if (!project) return;
    const response = await fanfictionApi.createChapter(project.id, {
      chapter_number: chapters.length + 1,
      title: `第 ${chapters.length + 1} 章`,
      content: "",
    });
    setChapters((current) => [...current, response.data]);
    setSelectedChapterId(response.data.id);
    setChapterContent(response.data.content ?? "");
    setStatus("章节已创建");
  }

  async function saveChapter() {
    if (!project || !selectedChapter) return;
    const response = await fanfictionApi.updateChapter(project.id, selectedChapter.id, { content: chapterContent });
    setChapters((current) => current.map((item) => item.id === response.data.id ? response.data : item));
    setStatus("章节已保存，并生成一个版本快照");
  }

  async function showRecentDiff() {
    if (!project || revisions.length < 2) return;
    const ordered = [...revisions].sort((left, right) => left.revision_number - right.revision_number);
    const response = await fanfictionApi.diff(project.id, ordered.at(-2)!.id, ordered.at(-1)!.id);
    setDiffText(response.data.diff || "（最近两版没有文本差异）");
  }

  async function rollback(revision: FanFictionRevision) {
    if (!project) return;
    const response = await fanfictionApi.rollback(project.id, revision.id);
    if (response.data.restored_project) {
      const latest = (await fanfictionApi.list(project.novel_id)).data.find((item) => item.id === project.id);
      if (latest) {
        setProject(latest);
        setProjects((current) => current.map((item) => item.id === latest.id ? latest : item));
      }
    } else {
      const chapterResponse = await fanfictionApi.chapters(project.id);
      setChapters(chapterResponse.data);
    }
    const revisionResponse = await fanfictionApi.revisions(project.id);
    setRevisions(revisionResponse.data);
    setStatus(`已回滚到版本 ${revision.revision_number}，并保留新的回滚快照`);
  }

  async function downloadRevision(revision: FanFictionRevision, format: "markdown" | "epub") {
    if (!project) return;
    try {
      const response = await fanfictionApi.exportRevision(project.id, revision.id, format);
      const url = window.URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${revision.title || "creative-project"}-v${revision.revision_number}.${format === "markdown" ? "md" : "epub"}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setStatus(`已导出版本 ${revision.revision_number}（${format.toUpperCase()}）`);
    } catch {
      setStatus("版本导出失败，请稍后重试");
    }
  }

  async function createOverride() {
    if (!project || !overrideKey.trim() || !overrideStatement.trim() || !overrideReason.trim()) return;
    const response = await fanfictionApi.createOverride(project.id, {
      override_key: overrideKey.trim(),
      statement: overrideStatement.trim(),
      reason: overrideReason.trim(),
    });
    setOverrides((current) => [response.data, ...current]);
    setOverrideKey("");
    setOverrideStatement("");
    setOverrideReason("");
    setStatus("已记录创作偏离；不会回写原作空间");
  }

  return (
    <section className="paper-surface rounded-3xl p-5 sm:p-7" aria-label="创作项目编辑器">
      <div className="flex flex-col gap-4 border-b border-border/60 pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Creative canon · local editor</p>
          <h2 className="mt-2 font-serif text-2xl font-semibold">建立一个属于你的分支</h2>
          <p className="mt-1 text-sm text-muted-foreground">Markdown 草稿与版本快照已开放；AI 续写仍保持明确延期。</p>
        </div>
        <span className="rounded-full border border-border bg-secondary px-3 py-1 text-xs text-muted-foreground">{status}</span>
      </div>

      <div className="mt-6 grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="space-y-4">
          <label className="block text-xs font-semibold text-muted-foreground">原作
            <select className="mt-2 w-full rounded-xl border border-border bg-background px-3 py-2 text-sm" value={selectedNovelId ?? ""} onChange={(event) => setSelectedNovelId(Number(event.target.value))}>
              <option value="">选择原作</option>
              {novels.map((novel) => <option key={novel.id} value={novel.id}>{novel.title}</option>)}
            </select>
          </label>
          <button type="button" className="w-full rounded-xl bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground" onClick={createProject} disabled={!selectedNovelId}>新建分支草稿</button>
          <div className="space-y-2" aria-label="草稿列表">
            {projects.map((item) => <button key={item.id} type="button" className={`w-full rounded-xl border px-3 py-2 text-left text-sm ${project?.id === item.id ? "border-primary bg-primary/10" : "border-border"}`} onClick={() => selectProject(item)}>{item.title}</button>)}
          </div>
        </aside>

        <div className="min-w-0 space-y-5">
          {project ? <>
            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <input className="rounded-xl border border-border bg-background px-3 py-2 font-serif text-lg" value={title} onChange={(event) => setTitle(event.target.value)} aria-label="草稿标题" />
              <button type="button" className="rounded-xl border border-primary px-4 py-2 text-sm font-semibold text-primary" onClick={saveProject}>保存项目</button>
            </div>
            <textarea className="min-h-48 w-full rounded-2xl border border-border bg-background p-4 font-mono text-sm leading-6" value={content} onChange={(event) => setContent(event.target.value)} aria-label="项目 Markdown 内容" placeholder="写下这条分支的设定、人物选择或章节提纲…" />
            <div className="rounded-2xl border border-border/70 p-4">
              <div className="flex items-center justify-between gap-3"><h3 className="font-serif text-lg font-semibold">章节规划</h3><button type="button" className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold" onClick={createChapter}>新增章节</button></div>
              <div className="mt-3 grid gap-3 md:grid-cols-[180px_minmax(0,1fr)]">
                <div className="space-y-2">{chapters.map((chapter) => <button key={chapter.id} type="button" className={`w-full rounded-lg px-3 py-2 text-left text-sm ${selectedChapterId === chapter.id ? "bg-secondary font-semibold" : "hover:bg-secondary/60"}`} onClick={() => { setSelectedChapterId(chapter.id); setChapterContent(chapter.content ?? ""); }}>第 {chapter.chapter_number} 章 · {chapter.title || "未命名"}</button>)}</div>
                {selectedChapter ? <div className="space-y-3"><textarea className="min-h-40 w-full rounded-xl border border-border bg-background p-3 font-mono text-sm leading-6" value={chapterContent} onChange={(event) => setChapterContent(event.target.value)} aria-label="章节 Markdown 内容" /><button type="button" className="rounded-lg bg-foreground px-3 py-2 text-xs font-semibold text-background" onClick={saveChapter}>保存章节</button></div> : <p className="rounded-xl bg-secondary/60 p-4 text-sm text-muted-foreground">选择或新增章节开始写作。</p>}
              </div>
            </div>
            <div className="rounded-2xl border border-border/70 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="font-serif text-lg font-semibold">版本历史</h3>
                <button type="button" className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold" onClick={showRecentDiff} disabled={revisions.length < 2}>查看最近 diff</button>
              </div>
              <div className="mt-3 space-y-2">
                {revisions.slice(0, 5).map((revision) => <div key={revision.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-secondary/60 px-3 py-2 text-xs"><span>v{revision.revision_number} · {revision.editor_kind} · {revision.chapter_id ? `第 ${chapters.find((chapter) => chapter.id === revision.chapter_id)?.chapter_number ?? "?"} 章` : "项目"}</span><div className="flex gap-2"><button type="button" className="font-semibold text-primary" onClick={() => downloadRevision(revision, "markdown")}>Markdown</button><button type="button" className="font-semibold text-primary" onClick={() => downloadRevision(revision, "epub")}>EPUB</button><button type="button" className="font-semibold text-primary" onClick={() => rollback(revision)}>回滚</button></div></div>)}
              </div>
              {diffText ? <pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-foreground p-3 text-xs leading-5 text-background">{diffText}</pre> : null}
            </div>
            <div className="rounded-2xl border border-border/70 p-4">
              <h3 className="font-serif text-lg font-semibold">创作偏离记录</h3>
              <p className="mt-1 text-xs text-muted-foreground">显式记录与原作不同的创作决定；只属于 Fanfiction Canon。</p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <input className="rounded-lg border border-border bg-background px-3 py-2 text-sm" value={overrideKey} onChange={(event) => setOverrideKey(event.target.value)} aria-label="偏离标识" placeholder="偏离标识，例如 choice:leave" />
                <input className="rounded-lg border border-border bg-background px-3 py-2 text-sm" value={overrideReason} onChange={(event) => setOverrideReason(event.target.value)} aria-label="偏离原因" placeholder="为什么选择偏离" />
              </div>
              <textarea className="mt-2 min-h-20 w-full rounded-lg border border-border bg-background p-3 text-sm" value={overrideStatement} onChange={(event) => setOverrideStatement(event.target.value)} aria-label="偏离决定" placeholder="写下这条偏离原作的决定…" />
              <button type="button" className="mt-2 rounded-lg border border-primary px-3 py-2 text-xs font-semibold text-primary" onClick={createOverride} disabled={!overrideKey.trim() || !overrideStatement.trim() || !overrideReason.trim()}>记录偏离</button>
              {overrides.length ? <ul className="mt-3 space-y-2">{overrides.slice(0, 5).map((override) => <li key={override.id} className="rounded-lg bg-secondary/60 px-3 py-2 text-xs"><span className="font-semibold">{override.override_key}</span>：{override.statement}<span className="ml-2 text-muted-foreground">({override.reason})</span></li>)}</ul> : null}
            </div>
            <p className="text-xs text-muted-foreground">当前内容属于 Fanfiction Canon，不会进入原作检索、评测、facet 或 Narrative Memory。</p>
          </> : <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">选择一本原作或创建第一份分支草稿。</div>}
        </div>
      </div>
    </section>
  );
}
