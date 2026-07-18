import { PageContainer } from "@/components/page-header";
import { Skeleton } from "@/components/ui/skeleton";

/** 通用「页头 + 列表」骨架，供 search/eval/settings/writing 复用的各自路由加载态参考形状。 */
export default function SearchLoading() {
  return (
    <PageContainer className="space-y-7">
      <div className="space-y-3 border-b border-border/70 pb-7">
        <Skeleton className="h-3 w-28" />
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <Skeleton className="h-16 w-full rounded-3xl" />
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-2xl" />
        ))}
      </div>
    </PageContainer>
  );
}
