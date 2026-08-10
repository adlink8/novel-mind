"use client";

/**
 * Per-capability provider gate (Phase 44, plan 44-03, Task 2, D-44-06/D-44-07).
 *
 * Renders provider-backed operations honestly: generation / embedding / image
 * actions are gated on the typed per-capability availability derived from local
 * runtime readiness, the REDACTED provider credential state and the last
 * provider request outcome — NOT one global online flag (T-44-03-03).
 *
 * - `available` → renders children (the provider operation UI).
 * - `unavailable` / `misconfigured` / `blocked` → renders an explicit
 *   blocked/unavailable panel with a stable reason. The operation is never
 *   executed, never returns an empty result, and never fabricates an artifact
 *   (D-44-07).
 *
 * Local (reader/editor/library/data) workflows are NOT wrapped by this gate —
 * they remain available offline (D-44-06).
 */
import { useEffect, useState, type ReactNode } from "react";
import { CloudOff, ShieldAlert, Sparkles } from "lucide-react";
import {
  getCapabilityStatus,
  type ProviderAvailability,
  type ProviderCapabilityKind,
  type ProviderCapabilityState,
} from "@/lib/runtime/capability-status";
import { cn } from "@/lib/utils";

const GATE_LABELS: Record<ProviderAvailability, string> = {
  available: "可用",
  unavailable: "不可用",
  blocked: "已阻断",
  misconfigured: "配置错误",
};

function statusTestId(availability: ProviderAvailability): string {
  return `provider-gate-${availability}`;
}

export interface ProviderCapabilityGateProps {
  /** Which provider capability this gate protects. */
  kind: ProviderCapabilityKind;
  children: ReactNode;
  className?: string;
}

export function ProviderCapabilityGate({
  kind,
  children,
  className,
}: ProviderCapabilityGateProps) {
  const [status, setStatus] = useState<ProviderCapabilityState | null>(null);

  useEffect(() => {
    let disposed = false;
    // Live status: reachability probe + redacted provider state + last request.
    void getCapabilityStatus()
      .then((capability) => {
        if (!disposed) setStatus(capability.providers[kind]);
      })
      .catch(() => {
        // Never fabricate a state on channel failure; degrade to blocked.
        if (!disposed) {
          setStatus({
            kind,
            availability: "blocked",
            reason: "无法读取能力状态，已保守阻断",
            lastRequest: null,
          });
        }
      });
    return () => {
      disposed = true;
    };
  }, [kind]);

  const blockReason =
    status !== null && status.availability !== "available" ? status.reason : null;

  if (status === null) {
    // First status still resolving — hold the provider UI, no fabricated state.
    return (
      <div
        data-testid="provider-gate-loading"
        className={cn("text-sm text-muted-foreground", className)}
      >
        正在检查能力状态…
      </div>
    );
  }

  if (status.availability === "available") {
    return <>{children}</>;
  }

  return (
    <div
      data-testid={statusTestId(status.availability)}
      data-kind={kind}
      role="status"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-border/60 bg-muted/40 px-3 py-2 text-sm",
        className,
      )}
    >
      {status.availability === "misconfigured" ? (
        <ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      ) : (
        <CloudOff className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      )}
      <div className="min-w-0">
        <p className="flex items-center gap-1.5 text-muted-foreground">
          <Sparkles className="size-3.5 shrink-0" aria-hidden />
          <span data-testid="provider-gate-label" className="font-medium text-foreground">
            {GATE_LABELS[status.availability]}
          </span>
          <span className="text-muted-foreground">· {kind}</span>
        </p>
        {blockReason ? (
          <p className="mt-0.5 text-xs text-muted-foreground" data-testid="provider-gate-reason">
            {blockReason}
          </p>
        ) : null}
      </div>
    </div>
  );
}
