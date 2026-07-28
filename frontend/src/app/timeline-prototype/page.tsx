"use client";

import { useState } from "react";
import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  CircleHelp,
  Expand,
  Eye,
  Filter,
  Search,
  Share2,
  Sparkles,
} from "lucide-react";

type Cluster = {
  title: string;
  chapters: string;
  events: number;
  height: number;
  tone: string;
};

const clusters: Cluster[] = [
  { title: "开端", chapters: "1–18 章", events: 86, height: 36, tone: "bg-[#9bbb92]" },
  { title: "哥布林村", chapters: "19–43 章", events: 142, height: 58, tone: "bg-[#6f946f]" },
  { title: "王国篇", chapters: "44–91 章", events: 186, height: 74, tone: "bg-[#567d5c]" },
  { title: "冲突升级", chapters: "92–137 章", events: 164, height: 64, tone: "bg-[#78966b]" },
  { title: "魔王篇", chapters: "138–188 章", events: 173, height: 69, tone: "bg-[#456e56]" },
  { title: "联盟篇", chapters: "189–239 章", events: 133, height: 52, tone: "bg-[#89a879]" },
  { title: "终局", chapters: "240–280 章", events: 114, height: 44, tone: "bg-[#66856a]" },
];

const localEvents = [
  ["利姆露救下三人", "第 7 章", "bottom"],
  ["哥布林村的危机", "第 8 章", "top"],
  ["牙狼族来袭", "第 9 章", "bottom"],
  ["命名仪式", "第 10 章", "top"],
  ["新的盟约", "第 11 章", "bottom"],
  ["村落扩建", "第 12 章", "top"],
] as const;

export default function TimelinePrototypePage() {
  const [drilledIn, setDrilledIn] = useState(false);
  const [selectedCluster, setSelectedCluster] = useState("哥布林村");

  return (
    <main className="mx-auto w-full max-w-[1540px] px-4 py-5 sm:px-6 lg:px-8">
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-amber-300/80 bg-amber-50 px-4 py-2.5 text-sm text-amber-950">
        <Eye className="size-4 shrink-0" />
        <span><strong>防剧透：</strong>未勾选「显示全书」时，仅展示你的阅读进度内的事件。</span>
      </div>

      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-1 text-xs font-medium tracking-[0.18em] text-primary">NOVEL ANALYSIS</p>
          <h1 className="font-serif text-3xl font-semibold tracking-tight">时间线</h1>
          <p className="mt-1 text-sm text-muted-foreground">从全书脉络进入一个章节范围，再查看具体事件。</p>
        </div>
        <button className="inline-flex h-10 items-center gap-2 rounded-xl border bg-card px-3 text-sm font-medium shadow-sm transition hover:border-primary hover:bg-primary/5">
          <BookOpen className="size-4" />
          关于这本书
        </button>
      </header>

      <nav className="mb-4 flex gap-2 overflow-x-auto" aria-label="分析工作区">
        {["时间线", "人物关系", "线索与伏笔"].map((tab, index) => (
          <button key={tab} className={`whitespace-nowrap rounded-full px-4 py-2 text-sm transition ${index === 0 ? "bg-foreground text-background" : "border bg-card text-muted-foreground hover:border-foreground/40"}`}>
            {tab}
          </button>
        ))}
      </nav>

      <section className="mb-4 flex flex-wrap items-center gap-2 rounded-2xl border bg-card/80 p-2.5 shadow-sm">
        <div className="flex rounded-xl bg-muted p-1">
          <button className="rounded-lg bg-background px-3 py-2 text-sm font-semibold shadow-sm">叙事顺序</button>
          <button className="rounded-lg px-3 py-2 text-sm text-muted-foreground">故事时间</button>
        </div>
        <button className="inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-sm text-muted-foreground"><Filter className="size-4" />全部人物<ChevronDown className="size-3.5" /></button>
        <button className="inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-sm"><Share2 className="size-4 text-[#a56d21]" />因果关系</button>
        <label className="ml-auto inline-flex h-9 items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 text-sm text-amber-950">
          <input type="checkbox" className="accent-[#b85b37]" /> 显示全书
        </label>
      </section>

      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_330px]">
        <div className="min-w-0 space-y-4">
          <section className="paper-surface overflow-hidden rounded-3xl p-4 sm:p-6" aria-labelledby="overview-title">
            <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#53745a]">Whole book overview</p>
                <h2 id="overview-title" className="mt-1 font-serif text-xl font-semibold">全书概览 <span className="font-sans text-base font-normal text-muted-foreground">· 998 个事件</span></h2>
              </div>
              <div className="flex items-center gap-1.5 rounded-full bg-[#eef4ec] px-3 py-1.5 text-xs font-medium text-[#486d50]"><CircleHelp className="size-3.5" />每块代表一个剧情阶段，而非单个事件</div>
            </div>

            <div className="rounded-2xl border bg-[#fcfdfb] p-4 sm:p-5">
              <div className="mb-4 flex items-end justify-between text-xs text-muted-foreground"><span>叙事推进</span><span>第 1 章 — 第 280 章</span></div>
              <div className="grid grid-cols-7 gap-2 sm:gap-3" aria-label="按剧情阶段聚合的全书时间线">
                {clusters.map((cluster) => (
                  <button
                    key={cluster.title}
                    type="button"
                    aria-pressed={selectedCluster === cluster.title}
                    onClick={() => { setSelectedCluster(cluster.title); setDrilledIn(true); }}
                    className={`group rounded-xl p-1.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${selectedCluster === cluster.title ? "bg-amber-100/80 ring-1 ring-amber-500" : "hover:bg-muted"}`}
                  >
                    <div className="flex h-28 items-end rounded-lg bg-[#edf0e9] px-1.5 pb-1.5">
                      <div className={`w-full rounded-md ${cluster.tone} transition group-hover:brightness-95`} style={{ height: `${cluster.height}%` }} />
                    </div>
                    <p className="mt-2 truncate text-xs font-semibold text-foreground">{cluster.title}</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">{cluster.events} 事件</p>
                  </button>
                ))}
              </div>
              <div className="mt-5 flex justify-between border-t pt-3 text-[11px] text-muted-foreground"><span>第 1 章</span><span>第 50 章</span><span>第 100 章</span><span>第 150 章</span><span>第 200 章</span><span>第 280 章</span></div>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50/65 px-4 py-3">
              <div><p className="text-sm font-semibold text-amber-950">当前聚合：{selectedCluster} · 第 7–12 章 · 42 个事件</p><p className="mt-0.5 text-xs text-amber-900/75">主要人物：利姆露、哥布塔、牙狼族 · 含 3 条因果连接</p></div>
              <button onClick={() => setDrilledIn(true)} className="inline-flex items-center gap-2 rounded-xl bg-[#3d684d] px-3.5 py-2 text-sm font-medium text-white transition hover:bg-[#31563f]"><Expand className="size-4" />展开查看</button>
            </div>
          </section>

          {drilledIn && (
            <section className="paper-surface rounded-3xl p-4 sm:p-6" aria-labelledby="detail-title">
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3"><button onClick={() => setDrilledIn(false)} className="rounded-xl border p-2 text-muted-foreground transition hover:text-foreground" aria-label="返回全书概览"><ArrowLeft className="size-4" /></button><div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#a56d21]">Zoomed range</p><h2 id="detail-title" className="mt-1 font-serif text-xl font-semibold">第 7–12 章 <span className="font-sans text-base font-normal text-muted-foreground">· 42 个事件</span></h2></div></div>
                <p className="text-xs text-muted-foreground">滚轮缩放 · 拖动平移 · 点击节点查看详情</p>
              </div>
              <div className="overflow-x-auto rounded-2xl border bg-[#fdfefc] p-4">
                <div className="relative min-w-[700px] pb-8 pt-6">
                  {[0, 1, 2, 3].map((lane) => <div key={lane} className="mb-8 border-t border-dashed border-[#d8e2d4]" />)}
                  <div className="absolute left-[6%] right-[5%] top-[43%] border-t-2 border-[#5c8366]" />
                  <div className="absolute left-[47%] top-[26%] h-[70px] w-[120px] rounded-tr-[90px] border-r-2 border-t-2 border-dashed border-[#b1792b]" />
                  <span className="absolute left-[62%] top-[19%] text-[10px] text-[#9a6820]">因果推进</span>
                  <div className="absolute inset-x-[5%] top-0 flex justify-between text-[11px] text-muted-foreground"><span>第 7 章</span><span>第 8 章</span><span>第 9 章</span><span>第 10 章</span><span>第 11 章</span><span>第 12 章</span></div>
                  <div className="absolute inset-x-[7%] top-[25%] flex justify-between">
                    {localEvents.map(([title, chapter, position], index) => (
                      <button key={title} className="group relative flex w-20 flex-col items-center text-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" style={{ transform: `translateY(${position === "top" ? -26 : index % 3 === 0 ? 40 : 24}px)` }}>
                        <span className={`mb-1.5 h-4 w-4 rounded-full border-[3px] border-white shadow-sm ${index === 3 ? "bg-[#c86d3f] ring-4 ring-orange-100" : "bg-[#4e805b]"}`} />
                        {(index === 0 || index === 3 || index === 5) && <><span className="line-clamp-2 text-xs font-semibold leading-4 text-foreground">{title}</span><span className="mt-0.5 text-[10px] text-muted-foreground">{chapter}</span></>}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"><span className="h-2.5 w-2.5 rounded-full bg-[#4e805b]" />普通事件 <span className="ml-3 h-2.5 w-2.5 rounded-full bg-[#c86d3f]" />当前事件 <span className="ml-3 border-t border-dashed border-[#b1792b] px-1" />因果关系</div>
            </section>
          )}

          <section className="paper-surface rounded-3xl p-4 sm:p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-serif text-xl font-semibold">当前范围事件</h2><p className="mt-0.5 text-xs text-muted-foreground">仅显示当前视窗内 42 个事件</p></div><button className="inline-flex items-center gap-1.5 text-sm font-medium text-primary">查看全部 <ChevronDown className="size-4" /></button></div>
            <div className="grid gap-3 md:grid-cols-3">
              {["利姆露命名哥布林村", "哥布林村开始扩建", "牙狼族首领战败"].map((title, index) => <button key={title} className={`rounded-2xl border p-4 text-left transition hover:-translate-y-0.5 hover:border-primary hover:shadow-sm ${index === 0 ? "border-primary/60 bg-primary/5" : "bg-card"}`}><p className="text-xs text-muted-foreground">第 {10 + index} 章 · 时间未知</p><h3 className="mt-1.5 font-serif text-base font-semibold">{title}</h3><p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">事件摘要与证据提示只在选择后展开，保持列表的可快速扫描性。</p></button>)}
            </div>
          </section>
        </div>

        <aside className="paper-surface h-fit rounded-3xl p-5 xl:sticky xl:top-5" aria-label="选中事件详情">
          <div className="mb-5 flex items-center justify-between"><span className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-semibold text-primary">第 10 章</span><button className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted" aria-label="关闭详情">×</button></div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#a56d21]">Selected event</p>
          <h2 className="mt-1 font-serif text-2xl font-semibold leading-snug">利姆露命名哥布林村</h2>
          <p className="mt-4 text-sm leading-7 text-muted-foreground">利姆露接受了村民的请求，为哥布林们赐名。村落由此获得新的归属，也成为后续发展和盟约的起点。</p>
          <div className="mt-5 border-t pt-4"><p className="text-xs font-semibold text-muted-foreground">参与人物</p><div className="mt-2 flex flex-wrap gap-2">{["利姆露", "哥布塔", "哥布林长老"].map((person) => <span key={person} className="rounded-full bg-muted px-2.5 py-1 text-xs">{person}</span>)}</div></div>
          <div className="mt-5 rounded-xl bg-[#f2f6f0] p-3"><p className="text-xs font-medium text-[#53745a]">为什么在这里显示？</p><p className="mt-1 text-xs leading-5 text-muted-foreground">它位于当前章节范围内；全书概览不会显示这个标题。</p></div>
          <div className="mt-5 grid gap-2"><button className="inline-flex items-center justify-center gap-2 rounded-xl border bg-card px-3 py-2.5 text-sm font-medium transition hover:border-primary"><Search className="size-4" />检索证据</button><button className="inline-flex items-center justify-center gap-2 rounded-xl bg-foreground px-3 py-2.5 text-sm font-medium text-background transition hover:bg-foreground/85"><BookOpen className="size-4" />阅读此章</button></div>
          <div className="mt-5 flex gap-2 border-t pt-4 text-xs text-muted-foreground"><Sparkles className="size-4 text-primary" />原型：聚合优先，按需展开</div>
        </aside>
      </div>
    </main>
  );
}
