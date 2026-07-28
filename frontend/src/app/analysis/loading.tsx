import { Skeleton } from "@/components/ui/skeleton";

/** 分析工作台为全视口壳，骨架按 侧栏 + 主图区 形状铺。 */
export default function AnalysisLoading() {
  return (
    <div className="grid h-full grid-cols-1 gap-4 p-4 lg:grid-cols-[16rem_1fr]">
      <div className="hidden space-y-3 lg:block">
        <Skeleton className="h-10 w-full rounded-2xl" />
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-7 w-full rounded-lg" />
        ))}
      </div>
      <div className="space-y-4">
        <Skeleton className="h-12 w-full rounded-2xl" />
        <Skeleton className="h-[60vh] w-full rounded-3xl" />
      </div>
    </div>
  );
}
