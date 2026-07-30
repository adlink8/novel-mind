import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { cn } from "@/lib/utils";
import {
  QUALITY_STATUS_LABELS,
  qualityStatusTone,
  type DeprecationMeta,
  type QualityJobPublic,
} from "@/lib/api";
import { ClipboardList } from "lucide-react";

const TONE_CLASS: Record<string, string> = {
  success: "bg-green-100 text-green-700",
  warning: "bg-yellow-100 text-yellow-800",
  danger: "bg-red-100 text-red-700",
  muted: "bg-gray-100 text-gray-600",
  info: "bg-blue-100 text-blue-700",
};

export function QualityStatusBadge({ status }: { status: string }) {
  const tone = qualityStatusTone(status);
  const label = QUALITY_STATUS_LABELS[status] || status;
  return (
    <Badge
      data-testid={`quality-status-${status}`}
      className={cn("text-xs", TONE_CLASS[tone])}
    >
      {label}
    </Badge>
  );
}

export function DeprecationBanner({ meta }: { meta: DeprecationMeta }) {
  if (!meta?.deprecated) return null;
  return (
    <div
      data-testid="deprecation-banner"
      className="rounded-xl border border-amber-300/60 bg-amber-50 px-4 py-3 text-xs text-amber-900"
    >
      <p className="font-semibold">Legacy Eval API 已弃用</p>
      <p className="mt-1">
        请迁移到 {meta.replacement?.status || "GET /api/eval/quality/runs/{job_id}"}。
        {meta.migration}
      </p>
    </div>
  );
}

export function QualityJobsPanel({
  jobs,
  onResume,
  onSelect,
  selected,
}: {
  jobs: QualityJobPublic[];
  onResume?: (jobId: string) => void;
  onSelect?: (jobId: string) => void;
  selected?: QualityJobPublic | null;
}) {
  if (jobs.length === 0) {
    return (
      <EmptyState
        icon={<ClipboardList className="size-6" />}
        title="暂无质量评测任务"
        description="在“评测运行”中选择小说并运行自动评测"
      />
    );
  }
  return (
    <div className="space-y-3" data-testid="quality-jobs-panel">
      {jobs.map((job) => (
        <Card key={job.job_id} className="paper-surface rounded-2xl">
          <CardContent className="flex items-start justify-between gap-3 p-4">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <QualityStatusBadge status={String(job.status)} />
                <span className="text-xs text-muted-foreground font-mono">
                  {job.job_id}
                </span>
                {!job.quality_comparable && (
                  <Badge variant="outline" className="text-xs">
                    metrics=null
                  </Badge>
                )}
              </div>
              {job.quality_comparable && job.metrics && (
                <p className="text-xs text-muted-foreground">
                  faithfulness={(job.metrics.answer_faithfulness ?? 0).toFixed(2)} ·
                  recall@5={(job.metrics.context_recall_at_5 ?? 0).toFixed(2)}
                </p>
              )}
              {job.error && (
                <p className="mt-1 text-xs text-red-600">{String(job.error)}</p>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => onSelect?.(job.job_id)}
              >
                查看报告
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onResume?.(job.job_id)}
              >
                恢复
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
      {selected && (
        <Card className="paper-surface rounded-2xl border-primary/30" data-testid="quality-job-report">
          <CardHeader>
            <CardTitle className="text-base">
              质量报告: {selected.job_id}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <QualityStatusBadge status={String(selected.status)} />
            <p>
              quality_comparable: {selected.quality_comparable ? "true" : "false"}
            </p>
            <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 text-xs">
              {JSON.stringify(
                selected.quality_comparable ? selected.metrics : null,
                null,
                2
              )}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
