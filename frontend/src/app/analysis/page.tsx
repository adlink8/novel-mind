"use client";

import { useEffect, useMemo, useState } from "react";
import { BookOpen, RefreshCw } from "lucide-react";

import { TimelineControls } from "@/components/timeline/timeline-controls";
import { TimelineStatus } from "@/components/timeline/timeline-status";
import { novelsApi, timelineApi, type Novel, type TimelineEnvelope, type TimelineOrdering, type TimelineRun, type TimelineVersionSource } from "@/lib/api";

function TimelinePreview({ events }: { events: NonNullable<TimelineEnvelope["active"]>["events"] }) {
  return <div data-testid="timeline-chart" className="grid gap-2 rounded-3xl border bg-card p-4">{events.map((event) => <div key={event.id} className="rounded-xl bg-muted p-3"><strong>{event.title}</strong><p className="text-sm text-muted-foreground">{event.description}</p></div>)}</div>;
}

export default function AnalysisPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelId, setNovelId] = useState("");
  const [ordering, setOrdering] = useState<TimelineOrdering>("narrative");
  const [person, setPerson] = useState("");
  const [causal, setCausal] = useState(false);
  const [fullBook, setFullBook] = useState(false);
  const [confirmFullBook, setConfirmFullBook] = useState(false);
  const [source, setSource] = useState<TimelineVersionSource>("active");
  const [envelope, setEnvelope] = useState<TimelineEnvelope>({ active: null, running_candidate: null });
  const [run, setRun] = useState<TimelineRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { novelsApi.list().then((response) => setNovels(response.data.items)).catch(() => setError("无法加载小说列表")); }, []);

  async function loadTimeline(id = novelId, next = { ordering, person, causal, fullBook }) {
    if (!id) return;
    const response = await timelineApi.getTimeline(id, { ordering: next.ordering, person: next.person || undefined, causal: next.causal, full_book: next.fullBook });
    setEnvelope(response.data);
    if (!response.data[source] && response.data.active) setSource("active");
    else if (!response.data[source] && response.data.running_candidate) setSource("running_candidate");
  }

  async function selectNovel(id: string) {
    setNovelId(id); setPerson(""); setSource("active"); setError("");
    if (!id) { setEnvelope({ active: null, running_candidate: null }); setRun(null); return; }
    setLoading(true);
    try {
      await timelineApi.startOrResume(id);
      const [statusResponse] = await Promise.all([timelineApi.status(id), loadTimeline(id)]);
      setRun(statusResponse.data);
    } catch { setError("时间线暂时无法加载，请稍后重试。"); }
    finally { setLoading(false); }
  }

  async function updateQuery(next: Partial<{ ordering: TimelineOrdering; person: string; causal: boolean; fullBook: boolean }>) {
    const query = { ordering, person, causal, fullBook, ...next };
    if (next.ordering) setOrdering(next.ordering);
    if (next.person !== undefined) setPerson(next.person);
    if (next.causal !== undefined) setCausal(next.causal);
    if (next.fullBook !== undefined) setFullBook(next.fullBook);
    try { await loadTimeline(novelId, query); } catch { setError("筛选结果更新失败。"); }
  }

  async function enableFullBook() {
    setConfirmFullBook(false);
    await timelineApi.setFullBookPreference(novelId, true);
    await updateQuery({ fullBook: true });
  }

  const selectedNovel = novels.find((novel) => String(novel.id) === novelId);
  const view = envelope[source];
  const people = useMemo(() => Array.from(new Set([...(envelope.active?.events ?? []), ...(envelope.running_candidate?.events ?? [])].flatMap((event) => event.participants.map((item) => item.mention)))).sort(), [envelope]);

  return (
    <div className="mx-auto grid w-full max-w-[1500px] gap-5 px-4 py-6 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">Novel analysis</p><h1 className="mt-1 font-serif text-3xl font-semibold sm:text-4xl">小说时间线</h1><p className="mt-2 max-w-2xl text-sm text-muted-foreground">按叙事或故事时间查看证据支持的事件。分析结果会逐章出现。</p></div>
        <label className="grid min-w-56 gap-1 text-xs text-muted-foreground">选择小说<select aria-label="选择小说" value={novelId} onChange={(event) => void selectNovel(event.target.value)} className="h-11 rounded-xl border bg-card px-3 text-sm text-foreground"><option value="">请选择一本小说</option>{novels.map((novel) => <option key={novel.id} value={novel.id}>{novel.title}</option>)}</select></label>
      </header>

      {!novelId ? <div className="grid min-h-72 place-items-center rounded-3xl border border-dashed bg-card/50 p-8 text-center"><div><BookOpen className="mx-auto mb-3 size-8 text-primary"/><h2 className="font-serif text-2xl font-semibold">选择小说开始分析</h2><p className="mt-2 text-sm text-muted-foreground">首次进入会自动开始或继续可恢复的时间线任务。</p></div></div> : (
        <>
          <TimelineStatus run={run} hasEvents={Boolean(envelope.active?.events.length || envelope.running_candidate?.events.length)} />
          {!selectedNovel?.reading_progress && !fullBook && <p className="rounded-xl border border-amber-300/70 bg-amber-50 px-4 py-3 text-sm text-amber-950">无阅读进度：为避免剧透，当前仅显示第一章 cutoff。</p>}
          <TimelineControls ordering={ordering} onOrderingChange={(value) => void updateQuery({ ordering: value })} people={people} person={person} onPersonChange={(value) => void updateQuery({ person: value })} causal={causal} onCausalChange={(value) => void updateQuery({ causal: value })} fullBook={fullBook} onFullBookRequest={(value) => value ? setConfirmFullBook(true) : void timelineApi.setFullBookPreference(novelId, false).then(() => updateQuery({ fullBook: false }))} />
          <div role="tablist" aria-label="分析版本" className="flex gap-2 overflow-x-auto">
            {envelope.active && <button role="tab" aria-selected={source === "active"} onClick={() => setSource("active")} className={`whitespace-nowrap rounded-full px-4 py-2 text-sm ${source === 'active' ? 'bg-foreground text-background' : 'border bg-card'}`}>当前版本 · v{envelope.active.version_id}</button>}
            {envelope.running_candidate && <button role="tab" aria-selected={source === "running_candidate"} onClick={() => setSource("running_candidate")} className={`whitespace-nowrap rounded-full px-4 py-2 text-sm ${source === 'running_candidate' ? 'bg-foreground text-background' : 'border bg-card'}`}><RefreshCw className="mr-1 inline size-3.5"/>正在生成 · v{envelope.running_candidate.version_id}</button>}
          </div>
          {error && <p role="alert" className="rounded-xl bg-destructive/10 p-4 text-sm text-destructive">{error}</p>}
          {loading ? <div className="h-80 animate-pulse rounded-3xl bg-muted" aria-label="正在加载时间线"/> : view ? <TimelinePreview events={view.events} /> : <div className="grid min-h-64 place-items-center rounded-3xl border border-dashed text-center text-muted-foreground">分析已启动，第一批事件生成后会显示在这里。</div>}
        </>
      )}

      {confirmFullBook && <div role="dialog" aria-modal="true" aria-label="确认显示全书" className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4"><div className="max-w-md rounded-3xl bg-background p-6 shadow-2xl"><h2 className="font-serif text-2xl font-semibold">确认显示全书</h2><p className="mt-3 text-sm text-muted-foreground">这会显示阅读进度之后的事件，可能包含重大剧透。偏好将按本书保存。</p><div className="mt-6 flex justify-end gap-2"><button className="rounded-xl border px-4 py-2 text-sm" onClick={() => setConfirmFullBook(false)}>取消</button><button className="rounded-xl bg-foreground px-4 py-2 text-sm text-background" onClick={() => void enableFullBook()}>确认显示全书</button></div></div></div>}
    </div>
  );
}
