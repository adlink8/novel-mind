/**
 * Runtime recovery panel (Phase 43, plan 43-04 renderer wiring, D-43-08/D-43-09).
 *
 * Renders an honest, bounded recovery surface for a non-ready desktop runtime:
 * the current lifecycle state, the failed component (when any) with its stable
 * redacted error code/message, and exactly the actions the shared contract
 * allowlists for that state (T-43-04-02).
 *
 * Defense in depth: even though `RuntimeRecoveryState.recoveryActions` is
 * derived main-side from the allowlist, the panel re-checks every action with
 * the local `isActionAllowed` mirror of the shared contract
 * (`desktop/src/shared/runtime-status.ts`) before rendering its button — the
 * renderer can never surface a free-form or out-of-state action string. The
 * contract module is imported type-only: it transitively imports desktop
 * runtime types, so a value import would leak desktop code into the web bundle
 * (same boundary pattern as `frontend/src/lib/desktop/runtime-recovery.ts`).
 *
 * This is a pure presentational component: the parent supplies the state and
 * routes `onAction` to the desktop authority, which re-validates before
 * executing anything.
 */
"use client";

import type {
  RecoveryActionId,
  RuntimeRecoveryState,
} from "../../../../desktop/src/shared/runtime-status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export interface RuntimeRecoveryPanelProps {
  state: RuntimeRecoveryState;
  /** Route a bounded recovery action to the desktop authority. */
  onAction: (actionId: RecoveryActionId) => void;
  /** Action currently executing (its button is disabled). */
  busyAction: RecoveryActionId | null;
  /** Optional redacted error from a failed/denied action. */
  actionError?: string | null;
}

const STATE_TITLES: Record<RuntimeRecoveryState["state"], string> = {
  stopped: "本地运行时已停止",
  starting: "正在启动本地运行时",
  migrating: "正在迁移本地数据",
  stopping: "正在停止本地运行时",
  ready: "本地运行就绪",
  degraded: "本地运行时降级运行",
  failed: "本地运行时启动失败",
};

function stateDescription(state: RuntimeRecoveryState["state"]): string {  switch (state) {
    case "starting":
      return "正在拉起本地服务，请稍候。";
    case "migrating":
      return "正在执行首次数据迁移，请勿关闭窗口。";
    case "stopping":
      return "本地服务正在停止。";
    case "stopped":
      return "本地服务尚未启动。启动后即可继续阅读、检索与创作。";
    case "degraded":
      return "部分本地服务不可用，产品数据可能不完整。请先修复运行时。";
    case "failed":
      return "本地运行时未能就绪。你可以重试，或打开诊断信息查看原因。";
    case "ready":
      return "本地运行时就绪。";
  }
}

/**
 * Renderer-safe mirror of `isActionAllowed` in the shared contract
 * (`desktop/src/shared/runtime-status.ts`). The contract module is imported
 * type-only across the bundle boundary; the allowlist logic is reimplemented
 * here verbatim so the renderer-side defense-in-depth check stays identical.
 */
function isActionAllowed(
  state: RuntimeRecoveryState["state"],
  actionId: RecoveryActionId,
  backupAvailable: boolean,
): boolean {
  switch (state) {
    case "stopped":
      return actionId === "retry" || actionId === "openDiagnostics";
    case "degraded":
      return (
        actionId === "retry" ||
        actionId === "restart" ||
        actionId === "openDiagnostics"
      );
    case "failed":
      return (
        actionId === "retry" ||
        actionId === "openDiagnostics" ||
        (backupAvailable && actionId === "restoreBackup")
      );
    default:
      // starting / migrating / ready / stopping: no action is safe.
      return false;
  }
}

export function RuntimeRecoveryPanel({
  state,
  onAction,
  busyAction,
  actionError,
}: RuntimeRecoveryPanelProps) {
  // Defense-in-depth allowlist check (T-43-04-02): never render a button for an
  // action the shared contract does not allow in this state.
  const allowedActions = state.recoveryActions.filter((action) =>
    isActionAllowed(state.state, action.id, state.backupAvailable),
  );

  return (
    <div
      role="status"
      aria-live="polite"
      data-state={state.state}
      data-testid="runtime-recovery-panel"
      className="grid min-h-[60vh] place-items-center p-6"
    >
      <Card size="sm" className="w-full max-w-md">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CardTitle>{STATE_TITLES[state.state]}</CardTitle>
            <Badge
              variant={state.state === "failed" ? "destructive" : "secondary"}
              data-testid="runtime-state-badge"
            >
              {state.state}
            </Badge>
          </div>
          <CardDescription>{stateDescription(state.state)}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {state.failedComponent !== null && (
            <p className="text-sm">
              失败组件：<code className="rounded bg-muted px-1.5 py-0.5">{state.failedComponent}</code>
            </p>
          )}
          {state.errorCode !== null && (
            <p className="text-sm text-muted-foreground">
              <span data-testid="runtime-error-code">{state.errorCode}</span>
              {state.errorMessage !== null && (
                <span className="ml-2">{state.errorMessage}</span>
              )}
            </p>
          )}
          {allowedActions.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              {allowedActions.map((action) => (
                <Button
                  key={action.id}
                  variant={action.id === "restoreBackup" ? "destructive" : "default"}
                  size="sm"
                  disabled={busyAction !== null}
                  onClick={() => onAction(action.id)}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          )}
          {actionError !== null && actionError !== undefined && (
            <p className="text-sm text-destructive" data-testid="runtime-action-error">
              {actionError}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
