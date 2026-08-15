/**
 * Runtime gate (Phase 43, plan 43-04 renderer wiring, D-43-08/D-43-09).
 *
 * Gates product routes behind the desktop runtime's honest recovery state.
 * Product content (library / novels / analysis / writing) renders ONLY when the
 * runtime is ready — a degraded or failed runtime renders the recovery panel
 * instead of an empty-success domain state (D-43-09).
 *
 * Degradation contract:
 * - Browser mode (no `window.novelMindDesktop` bridge): the default source is
 *   null and this gate renders nothing extra — plain children, no errors.
 * - Desktop mode: subscribes to the recovery source and renders the
 *   `RuntimeRecoveryPanel` for every non-ready state (stopped / starting /
 *   migrating / degraded / failed / stopping).
 * - Channel failure (source rejects while resolving): pass through children
 *   rather than fabricate a failure or block the app on a dead channel.
 *
 * The data source is injectable for tests and for the future managed-runtime
 * recovery channel; the default is `desktopRuntimeRecoverySource()`.
 */
"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import type {
  RecoveryActionId,
  RuntimeRecoveryState,
} from "../../../../desktop/src/shared/runtime-status";
import {
  desktopRuntimeRecoverySource,
  type RuntimeRecoverySource,
} from "@/lib/desktop/runtime-recovery";
import { RuntimeRecoveryPanel } from "./RuntimeRecoveryPanel";

export interface RuntimeGateProps {
  children: ReactNode;
  /**
   * Injectable recovery source (test seam / future managed-runtime channel).
   * Defaults to the desktop shell source; null forces plain degradation.
   */
  source?: RuntimeRecoverySource | null;
}

function LoadingRuntime({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="runtime-loading"
      className="grid min-h-[60vh] place-items-center text-sm text-muted-foreground"
    >
      {label}
    </div>
  );
}

export function RuntimeGate({ children, source }: RuntimeGateProps) {
  const activeSource = useMemo(
    () => (source === undefined ? desktopRuntimeRecoverySource() : source),
    [source],
  );

  const [state, setState] = useState<RuntimeRecoveryState | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busyAction, setBusyAction] = useState<RecoveryActionId | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (activeSource === null) return;

    let disposed = false;
    activeSource
      .getStatus()
      .then((next) => {
        if (!disposed) setState(next);
      })
      .catch(() => {
        // Dead channel: never fabricate a state; pass through conservatively.
        if (!disposed) setLoadFailed(true);
      });

    const unsubscribe = activeSource.subscribe((next) => {
      if (disposed) return;
      setState(next);
      setActionError(null);
    });

    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [activeSource]);

  // Browser mode / explicitly disabled gate: pure degradation, no gate UI.
  if (activeSource === null) return <>{children}</>;

  // Channel failed to resolve: do not block the app on a dead channel.
  if (loadFailed) return <>{children}</>;

  // First status still resolving — hold product content, no fabricated state.
  if (state === null) {
    return <LoadingRuntime label="正在连接本地运行时…" />;
  }

  // Domain children render only in the ready state (D-43-09).
  if (state.ready) return <>{children}</>;

  async function handleAction(actionId: RecoveryActionId) {
    if (activeSource === null) return;
    setBusyAction(actionId);
    setActionError(null);
    try {
      const result = await activeSource.request(actionId);
      if (!result.ok) setActionError(result.error);
      // Re-pull so a successful action reflects the new runtime state.
      const next = await activeSource.getStatus();
      setState(next);
    } catch {
      setActionError("恢复操作失败，请重试");
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <RuntimeRecoveryPanel
      state={state}
      onAction={(actionId) => void handleAction(actionId)}
      busyAction={busyAction}
      actionError={actionError}
    />
  );
}
