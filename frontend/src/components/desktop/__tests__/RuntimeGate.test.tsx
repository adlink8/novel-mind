/**
 * RuntimeGate component tests (Phase 43, plan 43-04 renderer wiring).
 *
 * Covers:
 * - Browser mode (no bridge): pure degradation — children render, no gate UI.
 * - Injected recovery source: ready renders children; starting / migrating /
 *   degraded / failed render the recovery panel with exactly the allowlisted
 *   actions (T-43-04-02) and never render domain children in non-ready states
 *   (D-43-09).
 * - Defense in depth: a state carrying an out-of-allowlist action does not
 *   surface that action's button.
 * - Default shell source against a mocked bridge (shell ready vs not-ready).
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RuntimeRecoverySource } from "@/lib/desktop/runtime-recovery";
import type {
  RecoveryAction,
  RecoveryActionId,
  RuntimeRecoveryState,
} from "../../../../../desktop/src/shared/runtime-status";
import { RuntimeGate } from "../RuntimeGate";

function baseState(): RuntimeRecoveryState {
  return {
    state: "stopped",
    ready: false,
    failedComponent: null,
    errorCode: null,
    errorMessage: null,
    recoveryActions: [],
    backupAvailable: false,
    startedAt: null,
  };
}

function action(id: RecoveryActionId): RecoveryAction {
  const labels: Record<RecoveryActionId, string> = {
    retry: "Retry",
    restart: "Restart service",
    openDiagnostics: "Open diagnostics",
    restoreBackup: "Restore backup",
  };
  return { id, label: labels[id], description: `action ${id}` };
}

function makeSource(initial: RuntimeRecoveryState) {
  let current = initial;
  const listeners = new Set<(state: RuntimeRecoveryState) => void>();
  const request = vi.fn<RuntimeRecoverySource["request"]>(async () => ({ ok: true }));
  const source: RuntimeRecoverySource = {
    getStatus: vi.fn(async () => current),
    subscribe: vi.fn((listener: (state: RuntimeRecoveryState) => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    }),
    request,
  };
  return {
    source,
    request,
    push(next: RuntimeRecoveryState) {
      current = next;
      listeners.forEach((listener) => listener(next));
    },
  };
}

const productContent = <p>产品内容</p>;

describe("RuntimeGate — browser mode (no bridge)", () => {
  it("renders children and no gate UI when the bridge is absent", () => {
    render(<RuntimeGate>{productContent}</RuntimeGate>);
    expect(screen.getByText("产品内容")).toBeInTheDocument();
    expect(screen.queryByTestId("runtime-recovery-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("runtime-loading")).not.toBeInTheDocument();
  });
});

describe("RuntimeGate — injected recovery source", () => {
  it("renders children only in the ready state (no panel)", async () => {
    const { source } = makeSource({ ...baseState(), state: "ready", ready: true });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    expect(await screen.findByText("产品内容")).toBeInTheDocument();
    expect(screen.queryByTestId("runtime-recovery-panel")).not.toBeInTheDocument();
  });

  it("renders the starting panel without domain children or actions", async () => {
    const { source } = makeSource({ ...baseState(), state: "starting" });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    const panel = await screen.findByTestId("runtime-recovery-panel");
    expect(panel).toHaveAttribute("data-state", "starting");
    expect(screen.getByText("正在启动本地运行时")).toBeInTheDocument();
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("renders the migrating panel", async () => {
    const { source } = makeSource({ ...baseState(), state: "migrating" });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    expect(await screen.findByText("正在迁移本地数据")).toBeInTheDocument();
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
  });

  it("renders degraded with exactly retry/restart/openDiagnostics and no children", async () => {
    const { source } = makeSource({
      ...baseState(),
      state: "degraded",
      failedComponent: "next",
      errorCode: "EXIT_EARLY",
      errorMessage: "component exited early",
      recoveryActions: [action("retry"), action("restart"), action("openDiagnostics")],
    });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    const panel = await screen.findByTestId("runtime-recovery-panel");
    expect(panel).toHaveAttribute("data-state", "degraded");
    expect(screen.getByText("EXIT_EARLY")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart service" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open diagnostics" })).toBeInTheDocument();
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
  });

  it("offers restoreBackup in failed when a verified backup exists", async () => {
    const { source } = makeSource({
      ...baseState(),
      state: "failed",
      failedComponent: "postgres_pgvector",
      errorCode: "MIGRATION_FAILED",
      errorMessage: "migration failed",
      recoveryActions: [action("retry"), action("openDiagnostics"), action("restoreBackup")],
      backupAvailable: true,
    });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByTestId("runtime-recovery-panel");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open diagnostics" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore backup" })).toBeInTheDocument();
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
  });

  it("never offers restoreBackup without a verified backup", async () => {
    const { source } = makeSource({
      ...baseState(),
      state: "failed",
      failedComponent: "vector_store",
      errorCode: "SPAWN_FAILED",
      errorMessage: "spawn failed",
      recoveryActions: [action("retry"), action("openDiagnostics")],
      backupAvailable: false,
    });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByTestId("runtime-recovery-panel");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open diagnostics" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restore backup" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart service" })).not.toBeInTheDocument();
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
  });

  it("surfaces a failed component and redacted error", async () => {
    const { source } = makeSource({
      ...baseState(),
      state: "failed",
      failedComponent: "fastapi",
      errorCode: "START_TIMEOUT",
      errorMessage: "start timed out",
      recoveryActions: [action("retry")],
    });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByTestId("runtime-recovery-panel");
    expect(screen.getByText("fastapi")).toBeInTheDocument();
    expect(screen.getByTestId("runtime-error-code")).toHaveTextContent("START_TIMEOUT");
  });

  it("does not render an action that the shared allowlist rejects for the state", async () => {
    // `restart` is NOT allowed in `stopped`; a hostile/stale state listing it
    // must not surface the button (defense in depth, T-43-04-02).
    const { source } = makeSource({
      ...baseState(),
      state: "stopped",
      recoveryActions: [action("retry"), action("restart")],
    });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByTestId("runtime-recovery-panel");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart service" })).not.toBeInTheDocument();
  });

  it("routes a recovery action to the source and re-pulls status", async () => {
    const { source, request, push } = makeSource({
      ...baseState(),
      state: "failed",
      recoveryActions: [action("retry")],
    });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByTestId("runtime-recovery-panel");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("retry"));

    // A successful action reflects the new runtime state (re-pull + push).
    await act(async () => {
      push({ ...baseState(), state: "ready", ready: true });
    });
    await waitFor(() => expect(screen.getByText("产品内容")).toBeInTheDocument());
    expect(screen.queryByTestId("runtime-recovery-panel")).not.toBeInTheDocument();
  });

  it("shows a redacted action error when the authority denies the action", async () => {
    const request = vi.fn<RuntimeRecoverySource["request"]>(async () => ({
      ok: false,
      error: "action retry is not allowed while runtime is failed",
    }));
    const { source } = makeSource({
      ...baseState(),
      state: "failed",
      recoveryActions: [action("retry")],
    });
    source.request = request;
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByTestId("runtime-recovery-panel");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByTestId("runtime-action-error")).toHaveTextContent(
        "action retry is not allowed",
      ),
    );
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
  });

  it("disables action buttons while a recovery action is in flight", async () => {
    let resolveRequest!: (value: { ok: true } | { ok: false; error: string }) => void;
    const request = vi.fn<RuntimeRecoverySource["request"]>(
      () => new Promise((resolve) => (resolveRequest = resolve)),
    );
    const { source } = makeSource({
      ...baseState(),
      state: "failed",
      recoveryActions: [action("retry")],
    });
    source.request = request;
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByTestId("runtime-recovery-panel");

    const retryButton = screen.getByRole("button", { name: "Retry" });
    fireEvent.click(retryButton);
    await waitFor(() => expect(retryButton).toBeDisabled());

    await waitFor(() => resolveRequest({ ok: true }));
    await waitFor(() => expect(retryButton).not.toBeDisabled());
  });

  it("propagates push status changes from the source", async () => {
    const { source, push } = makeSource({ ...baseState(), state: "ready", ready: true });
    render(<RuntimeGate source={source}>{productContent}</RuntimeGate>);
    await screen.findByText("产品内容");

    await act(async () => {
      push({
        ...baseState(),
        state: "degraded",
        failedComponent: "agent_service",
        errorCode: "EXIT_EARLY",
        recoveryActions: [action("retry"), action("restart"), action("openDiagnostics")],
      });
    });
    await waitFor(() =>
      expect(screen.getByTestId("runtime-recovery-panel")).toHaveAttribute(
        "data-state",
        "degraded",
      ),
    );
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
  });
});

describe("RuntimeGate — default shell source against a mocked bridge", () => {
  const SHELL_READY = {
    ready: true,
    appVersion: "0.1.0",
    electronVersion: "43.3.0",
    security: { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true },
  };

  function withBridge(ready: boolean) {
    (window as unknown as Record<string, unknown>)["novelMindDesktop"] = {
      getRuntimeStatus: async () => ({ ...SHELL_READY, ready }),
      requestRuntimeRestart: async () => ({ ok: true }),
      getBootstrap: async () => ({ appVersion: "0.1.0", bridgeVersion: 1, features: ["desktop-shell"] }),
      openExternalLink: async () => ({ ok: true }),
      onRuntimeStatus: () => ({ unsubscribe: () => {} }),
    };
  }

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>)["novelMindDesktop"];
  });

  it("passes children through when the shell bridge reports ready", async () => {
    withBridge(true);
    render(<RuntimeGate>{productContent}</RuntimeGate>);
    expect(await screen.findByText("产品内容")).toBeInTheDocument();
    expect(screen.queryByTestId("runtime-recovery-panel")).not.toBeInTheDocument();
  });

  it("shows an honest starting state while the shell is not ready", async () => {
    withBridge(false);
    render(<RuntimeGate>{productContent}</RuntimeGate>);
    const panel = await screen.findByTestId("runtime-recovery-panel");
    expect(panel).toHaveAttribute("data-state", "starting");
    expect(screen.getByText("正在启动本地运行时")).toBeInTheDocument();
    expect(screen.queryByText("产品内容")).not.toBeInTheDocument();
    // No fabricated actions on today's shell bridge.
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
