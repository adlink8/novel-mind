import { PageContainer } from "@/components/page-header";
import { Skeleton } from "@/components/ui/skeleton";

export default function NovelsLoading() {
  return (
    <PageContainer className="space-y-7">
      <div className="space-y-3 border-b border-border/70 pb-7">
        <Skeleton className="h-3 w-28" />
        <Skeleton className="h-9 w-40" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="paper-surface overflow-hidden rounded-3xl">
            <Skeleton className="h-40 w-full rounded-none" />
            <div className="space-y-3 p-4">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
              <div className="flex gap-2 pt-1">
                <Skeleton className="h-5 w-14 rounded-full" />
                <Skeleton className="h-5 w-16 rounded-full" />
              </div>
            </div>
          </div>
        ))}
      </div>
    </PageContainer>
  );
}
