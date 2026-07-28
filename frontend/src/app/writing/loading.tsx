import { PageContainer } from "@/components/page-header";
import { Skeleton } from "@/components/ui/skeleton";

export default function WritingLoading() {
  return (
    <PageContainer className="space-y-8">
      <div className="space-y-3 border-b border-border/70 pb-7">
        <Skeleton className="h-3 w-28" />
        <Skeleton className="h-9 w-44" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <Skeleton className="h-52 w-full rounded-[32px]" />
      <div className="grid gap-4 md:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-44 w-full rounded-3xl" />
        ))}
      </div>
    </PageContainer>
  );
}
